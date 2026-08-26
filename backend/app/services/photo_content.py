"""照片内容注册唯一编排（F1/P0-6 · 照片双轨收口）

背景（重构侦察 P0-6 / R1#2）：照片注册存在"双轨"——multipart 中转
（api/contents.py::upload_photo，perceptual_hash 409 去重 + moderate 预检）与
分片 complete（services/upload.py::register_photo_content，cos_key 幂等 + 无
moderate），去重键/护栏时机已分叉，同一契约两处维护。

本模块收敛唯一注册实现，参数化：
  - dedup_key：perceptual_hash（409 语义，Q16 同用户唯一）/ cos_key（同用户同
    cos_key 返回既有记录，P0-5）/ None（不查重）
  - moderate：bool——是否对 meta.text 做 pre-check（multipart 路径 True；分片
    路径 False，护栏由管线 CI 审核覆盖）
  - mode：original（新建真照片）/ thumbnail_meta（蜂窝：只落缩略图占位）/
    update（content_id 补传原件挂到既有占位）

两协议适配器（api/contents.py::upload_photo、services/upload.py::
register_photo_content）只做协议转换后委托本模块，两套幂等键都保留。对外错误
语义由适配器各自映射（API 层 ApiError CONTENT_002/003；服务层 services/errors
细粒度异常）。入队统一走 enqueue_unique（F4：同 content 键不重复入队）。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.queue import QUEUE_HIGH, enqueue_unique
from app.db.models import Content
from app.services import thumbnails
from app.services.errors import ConflictError, NotFoundError, ValidationError
from app.services.external.storage import best_effort_delete, get_storage_backend
from app.services.pipeline import process_content
from app.services.upload_meta import PhotoMeta

logger = logging.getLogger("yishu.photo_content")


class DuplicateError(ConflictError):
    """感知哈希重复（→ 409 CONTENT_002）：multipart 路径去重专用"""


class ModerateRejectError(ValidationError):
    """内容含敏感信息（→ 422 CONTENT_003）：moderate pre-check 拒绝"""


def _query_by_hash(db: Session, user_id: str, perceptual_hash: str) -> Content | None:
    """同用户同感知哈希（Q16，软删记录不参与去重）"""
    return db.execute(
        select(Content).where(
            Content.user_id == user_id,
            Content.perceptual_hash == perceptual_hash,
            Content.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def _query_by_cos_key(db: Session, user_id: str, cos_key: str) -> Content | None:
    """同用户同 cos_key（P0-5 幂等，软删记录不参与）"""
    return db.execute(
        select(Content).where(
            Content.user_id == user_id,
            Content.cos_key == cos_key,
            Content.deleted_at.is_(None),
        )
    ).scalar_one_or_none()


def _require_photo_bytes(cos_key: str) -> None:
    """照片原件魔数校验（P0-3 · 审查 H3）：分片 complete 路径校验文件类型

    伪装文件（`.jpg` 扩展名 + 任意字节）→ ValidationError（API 层 422），并尽力
    删除刚落的对象防孤儿；对象缺失 → NotFoundError（API 层 404）。
    """
    from app.services.file_magic import is_photo_bytes

    backend = get_storage_backend()
    try:
        data = backend.get_object(cos_key)
    except KeyError:
        raise NotFoundError("对象存储中未找到已上传文件") from None
    if not is_photo_bytes(data):
        best_effort_delete(cos_key, backend)
        raise ValidationError("文件内容与照片格式不符（魔数校验失败）")


def reflow_violation(db: Session, verdict: dict) -> None:
    """违规词回流（B5b）：moderate 命中（reject）→ 命中词写 SensitiveWord(level=3)

    自动入规则表（幂等；失败仅记录，不阻断用户请求）。原 api/contents.py 私有
    函数下沉至此，multipart 与 create_content 共享单一实现。
    """
    try:
        from app.services.llm_ops.guard import reflow_violation_words

        reflow_violation_words(db, verdict.get("matched") or [])
    except Exception:  # noqa: BLE001
        logger.warning("违规词回流失败（不阻断请求）", exc_info=True)


def safe_enqueue_unique(func, key: str, *args, queue_name: str = QUEUE_HIGH, **kwargs) -> None:
    """入队失败不阻断（P0-5）：内容已建，管线可异步补投——失败仅记日志。

    同 key 不重复入队（F4：job 级去重，见 core/queue.enqueue_unique）。
    """
    try:
        enqueue_unique(func, key, *args, queue_name=queue_name, **kwargs)
    except Exception as exc:  # noqa: BLE001 —— Redis 故障不否定已落库内容
        logger.warning(
            "内容入队失败 func=%s key=%s 内容已建（管线可异步补投）: %s",
            getattr(func, "__name__", func),
            key,
            exc,
        )


def _enqueue_pipeline(content_id: str, enqueue_thumbnail: bool) -> None:
    """照片注册后的管线入队：process_content（必须）+ 缩略图（分片路径可选）。

    enqueue_unique 同 content 键不重复入队（F4/R5-4#5）。
    """
    safe_enqueue_unique(process_content, content_id)
    if enqueue_thumbnail:
        safe_enqueue_unique(thumbnails.generate_thumbnail_job, content_id)


def register_photo_content(
    db: Session,
    user_id: str,
    *,
    dedup_key: str | None = None,
    moderate: bool = False,
    mode: str = "original",
    meta_obj: dict,
    photo_meta: PhotoMeta,
    perceptual_hash: str | None = None,
    cos_key: str | None = None,
    data: bytes | None = None,
    ext: str = ".jpg",
    exif_taken_at: datetime | None = None,
    extra: dict | None = None,
    content_id: str | None = None,
    enqueue_thumbnail: bool = False,
) -> str:
    """照片内容注册唯一编排：去重 → 护栏 → 建/更新记录 → 入队（返回 content_id）

    参数化（F1/P0-6）：
      - dedup_key="perceptual_hash"：multipart 路径，重复 → DuplicateError（409）
      - dedup_key="cos_key"：分片路径，同用户同 cos_key → 返回既有记录（幂等）
      - moderate=True：multipart 路径对 meta.text 做 moderate pre-check
        （reject → ModerateRejectError；命中词回流规则表）
      - mode="thumbnail_meta"：蜂窝占位（只落缩略图，status=done，不进管线）
      - mode="update"：content_id 补传原件挂到既有占位（触发完整管线）
      - mode="original"：新建真照片（multipart 传 data 落 storage；分片传 cos_key）
    """
    # 1. 去重（Q16 / P0-5）：两套幂等键保留；update 模式不做 cos_key 幂等（按 id 更新）
    if dedup_key == "perceptual_hash" and perceptual_hash:
        dup = _query_by_hash(db, user_id, perceptual_hash)
        if dup is not None:
            raise DuplicateError("重复内容（感知哈希已存在）")
    elif dedup_key == "cos_key" and mode != "update":
        existing = _query_by_cos_key(db, user_id, cos_key)
        if existing is not None:
            return str(existing.id)

    # 2. 护栏 pre-check（multipart 路径；分片路径由管线 CI 审核覆盖）
    if moderate:
        from app.services.external.dashscope import moderate as _moderate

        check_text = (meta_obj.get("text") or "").strip()
        if check_text:
            verdict = _moderate(check_text)
            if verdict.get("action") == "reject":
                reflow_violation(db, verdict)
                raise ModerateRejectError(
                    f"内容含敏感信息未保存：{verdict.get('reason', '')}"
                )

    taken_at = exif_taken_at if exif_taken_at is not None else photo_meta.taken_at
    backend = get_storage_backend()

    # 3. thumbnail_meta：上传物即缩略图 → 只落缩略图占位（等 WiFi 补传原件）
    if mode == "thumbnail_meta":
        thumbnail_key = thumbnails.derive_thumbnail_key(cos_key)
        try:
            thumb = thumbnails.resize_to_jpeg(backend.get_object(cos_key))
        except KeyError:
            raise NotFoundError("对象存储中未找到已上传文件") from None
        except ValueError as exc:
            raise ValidationError(str(exc)) from exc
        backend.put_object(thumbnail_key, thumb)
        extra = {**(extra or {}), "original_pending": True}
        record = Content(
            user_id=user_id,
            content_type="photo",
            taken_at=taken_at,
            gps_lat=photo_meta.gps_lat,
            gps_lng=photo_meta.gps_lng,
            cos_key=cos_key,
            thumbnail_key=thumbnail_key,
            extra=extra,
            source=photo_meta.source,
            status="done",  # 占位即可浏览（缩略图）；原件补传后转 processing 走管线
        )
        db.add(record)
        try:
            db.commit()
        except Exception:  # noqa: BLE001 —— P0-6：提交失败尽力删缩略图防孤儿
            db.rollback()
            best_effort_delete(thumbnail_key, backend)
            raise
        db.refresh(record)
        return str(record.id)

    # 4. update：content_id 指向 thumbnail_meta 占位内容 → 挂原件触发完整管线
    if mode == "update":
        existing = db.get(Content, content_id)
        if existing is None or str(existing.user_id) != str(user_id):
            raise NotFoundError("content_id 不存在或不属于当前用户")
        if data is None:
            _require_photo_bytes(cos_key)  # P0-3：补传原件同样魔数校验
        existing.cos_key = cos_key
        existing.status = "processing"
        # 合并（不覆盖占位期既有 extra，如 wechat 追溯/元数据）
        existing.extra = {**(existing.extra or {}), **(extra or {})}
        existing.extra.pop("original_pending", None)
        try:
            db.commit()
        except Exception:  # noqa: BLE001 —— P0-6：提交失败尽力删原件防孤儿
            db.rollback()
            best_effort_delete(cos_key)
            raise
        _enqueue_pipeline(str(existing.id), enqueue_thumbnail)
        return str(existing.id)

    # 5. original：新建真照片记录（multipart 传 data 落 storage / 分片用既有 cos_key）
    if data is not None:
        cos_key = f"photos/{user_id}/{uuid.uuid4().hex}{ext}"
        backend.put_object(cos_key, data)
    else:
        _require_photo_bytes(cos_key)  # P0-3：original 新建路径魔数校验
    record = Content(
        user_id=user_id,
        content_type="photo",
        taken_at=taken_at,
        gps_lat=photo_meta.gps_lat,
        gps_lng=photo_meta.gps_lng,
        perceptual_hash=perceptual_hash,
        cos_key=cos_key,
        extra=extra,
        source=photo_meta.source,
        status="processing",
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # 并发同哈希上传 → 唯一约束冲突 → 回滚重查，返回 409（与 create_content 一致）
        db.rollback()
        # P0-6（审查 H-3）：对象已落存储但 DB 无记录 → 尽力删除防孤儿对象
        best_effort_delete(cos_key)
        if dedup_key == "perceptual_hash" and perceptual_hash:
            dup = _query_by_hash(db, user_id, perceptual_hash)
            if dup is not None:
                raise DuplicateError("重复内容（感知哈希已存在）") from None
        raise
    except Exception:  # noqa: BLE001 —— P0-6：提交失败尽力删原件防孤儿
        db.rollback()
        best_effort_delete(cos_key)
        raise
    db.refresh(record)
    _enqueue_pipeline(str(record.id), enqueue_thumbnail)
    return str(record.id)
