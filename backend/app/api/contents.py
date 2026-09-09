"""内容路由：四类素材上传主链路（API-002）+ 相册直传（COS STS，决策 #10）

真实 DB 接入（S1-02）：
- contents 表入库 + perceptual_hash 去重（Q16，同用户唯一）
- RQ 入队异步 AI 管线（API-016：收件→转写→分类→聚类；API 立即返回）
- 分页游标（API-006）

客户端第一波（2026-08-24，B-BE-1/2）：
- POST /api/v1/contents/upload：照片 multipart 中转上传（客户端→后端→storage→contents→管线）
- 复用 create_content 的去重（409）/护栏（moderate）/类型白名单语义
"""
import array
import io as _io
import logging
import struct
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import Depends, File, Form, Query, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import PageParams, get_current_user, pagination_params, uuid4_str
from app.core.errors import (
    ERR_CONTENT_001,
    ERR_CONTENT_002,
    ERR_CONTENT_003,
    ERR_CONTENT_005,
    ERR_CONTENT_006,
    ERR_CONTENT_007,
    ERR_CONTENT_008,
    ERR_CONTENT_009,
    ERR_MEDIA_003,
    ERR_PROFILE_SENSITIVE_001,
    ERR_PROFILE_SENSITIVE_002,
    ERR_PROFILE_SENSITIVE_003,
    ApiError,
)
from app.core.queue import (
    DEFAULT_JOB_TIMEOUT,
    QUEUE_LOW,
    enqueue_unique,
)
from app.db.models import Content, ProfileSensitive, User
from app.db.session import get_db
from app.schemas.common import ApiResponse, Page
from app.schemas.content import (
    ContentCreate,
    ContentOut,
    ProfileSensitiveCreate,
    ProfileSensitiveDeleteOut,
    ProfileSensitiveOut,
)
from app.services.errors import NotFoundError
from app.services.external.storage import StorageError, get_storage_backend
from app.services.file_magic import is_photo_bytes
from app.services.photo_content import (
    DuplicateError,
    ModerateRejectError,
    reflow_violation,
)
from app.services.photo_content import (
    register_photo_content as photo_register,
)
from app.services.pipeline import process_content
from app.services.sync_common import parse_ts
from app.services.upload_meta import (
    ALLOWED_PHOTO_EXTS,
    MAX_PHOTO_BYTES,
    MetaValidationError,
    parse_photo_meta,
)

logger = logging.getLogger("yishu.contents")

router = make_router(prefix="/api/v1/contents", tags=["contents"])

# 画像级敏感对话式增删查（B1-6 / B5b FIX-4）：独立 router（prefix /api/v1/profile），
# 需集成 Agent 在 main.py 注册：app.include_router(profile_sensitive_router)。
profile_sensitive_router = make_router(prefix="/api/v1/profile", tags=["profile-sensitive"])

# 照片上传共享常量（TD-P2B · S1-H2 收口：MAX_PHOTO_BYTES/ALLOWED_PHOTO_EXTS 收敛到
# services/upload_meta.py，与分片链路 register_photo_content 同源；此处保留模块级名字
# 供 upload_photo 引用与测试 monkeypatch（test_content_upload 缩小上限用））
# PIL 解压炸弹防护（P0-3 · 审查 H3）：40MP 上限（超限拒绝解码）
_MAX_IMAGE_PIXELS = 40_000_000


def _extract_exif_datetime(data: bytes) -> datetime | None:
    """从照片字节提取 EXIF DateTimeOriginal（相机拍摄时间真值）

    客户端 DATE_TAKEN 可能被 MediaProvider 写成扫描时间（2026-08-24 真机实测），
    故以后端 EXIF 解析为准：EXIF 无时区=相机本地时间（本设备 +08），
    显式按 UTC+08:00 解释，与客户端 isoString(+08:00) 一致。
    P0-3（审查 H3）：Image.open 前设 MAX_IMAGE_PIXELS 防解压炸弹（getexif 虽不
    解码像素，但 open 阶段的尺寸检查会触发 DecompressionBombError）。
    """
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = _MAX_IMAGE_PIXELS
        img = Image.open(_io.BytesIO(data))
        exif = img.getexif()
        raw = exif.get(36867)  # DateTimeOriginal
        if raw:
            return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(
                tzinfo=timezone(timedelta(hours=8))
            )
    except Exception:  # noqa: BLE001 —— 非 JPEG/无 EXIF 静默降级
        return None
    return None


def _validate_cos_key(db: Session, user_id: str, cos_key: str) -> None:
    """TD-P3 M4（审查中危）：create_content 自供 cos_key 归属/前缀/存在性校验

    仅允许本用户前缀（photos|voice|thumbnails/{user_id}/）且对象已存在
    （或已登记为本用户同 cos_key 内容——幂等回退），否则 422 CONTENT_009。
    防跨租户对象拉进自己管线（M4：已知他人 key 可被处理留存）与任意 key 触发存储遍历。
    """
    allowed = (f"photos/{user_id}/", f"voice/{user_id}/", f"thumbnails/{user_id}/")
    if not cos_key.startswith(allowed):
        raise ApiError(ERR_CONTENT_009, "cos_key 非法或不属于当前用户", http=422)
    from app.services.external.storage import get_storage_backend

    if get_storage_backend().object_exists(cos_key):
        return
    # 幂等回退：对象可能已被搬移/清理，但同用户已登记同 cos_key 内容（旧客户端重复建）
    existing = db.scalar(
        select(Content).where(
            Content.user_id == user_id,
            Content.cos_key == cos_key,
            Content.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return
    raise ApiError(ERR_CONTENT_009, "cos_key 指向的对象不存在", http=422)


def _resolve_size_bytes(req: ContentCreate) -> int | None:
    """A9（2026-09-09 缺口收口）：确定入库的原件字节数

    优先级：客户端显式声明（ContentCreate.size_bytes，可选字段）>
    带 cos_key 时从存储实测（voice 单段链路字节已在存储里）> None。
    实测失败（KeyError/存储故障等）降级 None——不阻塞建记录，
    与分片 complete 主链（services/upload/register.py size_bytes=len(data)）对齐口径。
    """
    if req.size_bytes is not None:
        return req.size_bytes
    if not req.cos_key:
        return None
    try:
        return len(get_storage_backend().get_object(req.cos_key))
    except Exception:  # noqa: BLE001 —— 降级不阻塞建记录（object_exists 已过存在性校验，此处防并发删对象/存储抖动）
        logger.warning("size_bytes 存储实测失败（降级 None 不阻塞入库）key=%s", req.cos_key)
        return None


@router.post("/upload", response_model=ApiResponse[ContentOut])
def upload_photo(
    file: UploadFile = File(...),
    meta: str = Form("{}"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """照片中转上传（客户端第一波 B3/B4：multipart → storage → contents → 管线）

    multipart 表单：
      file: 照片文件（jpg/jpeg/png/webp/heic/heif，≤20MB）
      meta: JSON 字符串 {taken_at, gps_lat, gps_lng, perceptual_hash, source, extra}

    语义与 create_content 对齐：perceptual_hash 409 去重 / moderate 护栏 /
    source 白名单；照片原件落 storage（cos_key），随后入队 process_content。
    F1/P0-6 双轨收口：去重/护栏/建记录/入队统一委托 services/photo_content.py
    （dedup_key="perceptual_hash" + moderate=True），本函数只做协议适配。
    """
    # 1+3. meta 解析与字段校验（TD-P2B · S1-H2 收口：与分片链路 register_photo_content
    #      同一契约——taken_at ISO/gps 边界/source 白名单统一走 upload_meta.parse_photo_meta；
    #      字段契约错误优先返回 CONTENT_005）
    try:
        photo_meta = parse_photo_meta(meta)
    except MetaValidationError as exc:
        raise ApiError(ERR_CONTENT_005, str(exc), http=422) from exc
    meta_obj = photo_meta.raw

    # 2. 文件校验（类型白名单 + 非空 + 大小上限 + 魔数嗅探）
    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if ext not in ALLOWED_PHOTO_EXTS and not content_type.startswith("image/"):
        raise ApiError(ERR_CONTENT_006, "仅支持照片文件（jpg/png/webp/heic）", http=422)
    data = file.file.read()
    if not data:
        raise ApiError(ERR_CONTENT_006, "文件为空", http=422)
    if len(data) > MAX_PHOTO_BYTES:
        raise ApiError(
            ERR_CONTENT_007,
            f"照片超过大小上限（{MAX_PHOTO_BYTES // 1024 // 1024}MB）",
            http=413,
        )
    # P0-3（审查 H3）：魔数校验——扩展名/content_type 头均不可信，
    # `.jpg` 文件名 + 任意字节（HTML/脚本）必须拒（防内容投毒）
    if not is_photo_bytes(data):
        raise ApiError(
            ERR_CONTENT_006, "文件内容与照片格式不符（魔数校验失败）", http=422
        )

    perceptual_hash = meta_obj.get("perceptual_hash") or None
    extra = meta_obj.get("extra") if isinstance(meta_obj.get("extra"), dict) else None

    # 3+4+5. 照片注册唯一编排（F1/P0-6 双轨收口）：去重（perceptual_hash 409）→
    #       moderate 护栏 → 原件落 storage → 建记录 → enqueue_unique 入队，
    #       统一委托 services/photo_content.py（参数化 dedup_key/moderate/mode）。
    #       EXIF 拍摄时间优先（相机真值；客户端时间可能被扫描污染）。
    exif_taken = _extract_exif_datetime(data)
    ext_safe = ext if ext in ALLOWED_PHOTO_EXTS else ".jpg"
    try:
        content_id = photo_register(
            db,
            user.id,
            dedup_key="perceptual_hash",
            moderate=True,
            mode="original",
            meta_obj=photo_meta.raw,
            photo_meta=photo_meta,
            perceptual_hash=perceptual_hash,
            data=data,
            ext=ext_safe,
            exif_taken_at=exif_taken,
            extra=extra,
            enqueue_thumbnail=False,
        )
    except DuplicateError as exc:
        raise ApiError(ERR_CONTENT_002, exc.message, http=409) from exc
    except ModerateRejectError as exc:
        raise ApiError(ERR_CONTENT_003, exc.message, http=422) from exc
    record = db.get(Content, content_id)
    return ApiResponse(data=_to_out(record))


@router.post("", response_model=ApiResponse[ContentOut])
def create_content(
    req: ContentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容入库：POST → contents 表 → 异步 AI 管线（RQ）→ 状态回写（API-002/API-016）"""
    if req.content_type not in ("photo", "text", "voice", "article"):
        raise ApiError(ERR_CONTENT_001, "不支持的 content_type", http=422)

    # R4#4（创建端点幂等键）：同用户 client_generated_id 已存在 → 幂等返回既有记录
    # （双击/网络重试不重复入库；photo/voice 既有幂等——perceptual_hash 409 / cos_key——
    #  保留为兜底，见下方去重与 voice 分支）
    if req.client_generated_id:
        existing = db.scalar(
            select(Content).where(
                Content.user_id == user.id,
                Content.client_generated_id == req.client_generated_id,
                Content.deleted_at.is_(None),
            )
        )
        if existing is not None:
            return ApiResponse(data=_to_out(existing))

    # TD-P3 M4（审查中危）：自供 cos_key 归属/前缀/存在性校验（防跨租户对象拉取）
    if req.cos_key:
        _validate_cos_key(db, user.id, req.cos_key)

    # 去重（Q16）：同用户 perceptual_hash 唯一（仅照片类有哈希；软删记录不参与，
    # 修复：原实现未过滤 deleted_at → 删除后重传同照片被 409 永久拒绝）
    if req.perceptual_hash:
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == req.perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError(ERR_CONTENT_002, "重复内容（感知哈希已存在）", http=409)

    # 护栏检测（B5b · 2026-08-20 接入普通入库）：reject → 拒绝入库；mask → 打码后入库
    from app.services.external.dashscope import moderate

    check_text = req.text or ""
    if check_text.strip():
        verdict = moderate(check_text)
        if verdict.get("action") == "reject":
            reflow_violation(db, verdict)
            raise ApiError(ERR_CONTENT_003, f"内容含敏感信息未保存：{verdict.get('reason', '')}", http=422)
        if verdict.get("action") == "mask" and verdict.get("masked_text"):
            req.text = verdict["masked_text"]

    # B5a 集成（Wave4 AgentJ 需求 1 配套）：voice 带 cos_key 时幂等——complete 已建 voice
    # 内容，旧客户端仍会二次调用本端点（saveVoiceContent），按 同用户+同 cos_key 去重返回既有记录
    if req.content_type == "voice" and req.cos_key:
        existing_voice = db.scalar(
            select(Content).where(
                Content.user_id == user.id,
                Content.cos_key == req.cos_key,
                Content.deleted_at.is_(None),
            )
        )
        if existing_voice is not None:
            return ApiResponse(data=_to_out(existing_voice))

    record = Content(
        user_id=user.id,
        content_type=req.content_type,
        text=req.text,
        taken_at=req.taken_at,
        gps_lat=req.gps_lat,
        gps_lng=req.gps_lng,
        perceptual_hash=req.perceptual_hash,
        client_generated_id=req.client_generated_id,
        cos_key=req.cos_key,
        thumbnail_key=req.thumbnail_key,
        extra=req.extra,
        source=req.source,
        status="processing",   # AI 管线完成后回写 done（异步）
        # BA1（迁移 f2a3b4c5d6e7）：用户备注（save 系可选上送）
        remark=req.remark,
        # A9（2026-09-09）：原件字节数——客户端声明优先，否则带 cos_key 时实测存储对象
        size_bytes=_resolve_size_bytes(req),
        # BA1：voice 走本端点时 duration 从客户端 extra.duration_ms（毫秒）换算秒——
        # 主落库点在 services/upload/register.py（complete 直建链路），此处为
        # POST /contents 二次调用（B5a saveVoiceContent）兜底对齐
        duration=(
            int(req.extra["duration_ms"]) // 1000
            if req.content_type == "voice"
            and isinstance(req.extra, dict)
            and req.extra.get("duration_ms") is not None
            else None
        ),
    )
    db.add(record)
    try:
        db.commit()
    except IntegrityError:
        # 并发冲突（client_generated_id 幂等键 / perceptual_hash 去重）→ 回滚重查
        db.rollback()
        # R4#4：并发同 client_generated_id → 唯一约束冲突 → 幂等返回既有记录
        if req.client_generated_id:
            dup = db.execute(
                select(Content).where(
                    Content.user_id == user.id,
                    Content.client_generated_id == req.client_generated_id,
                    Content.deleted_at.is_(None),
                )
            ).scalar_one_or_none()
            if dup is not None:
                return ApiResponse(data=_to_out(dup))
        # 审查修复(P1-04)：并发同哈希上传 → 唯一约束冲突 → 回滚重查，返回 409
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == req.perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError(ERR_CONTENT_002, "重复内容（感知哈希已存在）", http=409) from None
        raise
    db.refresh(record)

    # 入队异步 AI 管线（API-016；API 立即返回不阻塞）
    # 审查修复(P1-12)：voice/photo 用户等待 → 高优队列；text/article 低优
    # F4/R5-4#5：enqueue_unique 同 content 键不重复入队（job 级去重）
    if req.content_type in ("voice", "photo"):
        # R9-B6（2026-09-06）：第 2 个 str(record.id) 是函数参数——enqueue_unique 的 key
        # 只是去重键，缺 args = process_content() 零参 TypeError 秒死（failed 262 条同因）
        enqueue_unique(process_content, str(record.id), str(record.id))
    else:
        enqueue_unique(
            process_content,
            str(record.id),
            str(record.id),
            queue_name=QUEUE_LOW,
            job_timeout=DEFAULT_JOB_TIMEOUT,
        )

    return ApiResponse(data=_to_out(record))


@router.get("", response_model=ApiResponse[Page[ContentOut]])
def list_contents(
    content_id: str | None = None,
    page: PageParams = Depends(pagination_params),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容分页列表（API-006 游标分页：数据增删中翻页不错位）

    修复（审查 MAJOR）：原游标仅 created_at，同秒多条翻页错位/重复；
    改为 (created_at, id) 复合游标（id 兜底，UUID 不可比 → 用字符串序）。
    游标格式："<created_at_iso>|<id>"。
    R4#6/#7（分页统一）：limit/cursor 走共享 pagination_params（limit 1..100，cursor 不透明字符串）。
    """
    limit = page.limit
    query = select(Content).where(
        Content.user_id == user.id,
        Content.deleted_at.is_(None),
    ).order_by(Content.created_at.desc(), Content.id.desc())
    # 详情页精确取单条（content_id 为 UUID 字符串；路径 /contents/{id} 已归缩略图端点）
    if content_id:
        query = query.where(Content.id == content_id)

    if page.cursor:
        try:
            cursor_ts_raw, cursor_id = page.cursor.split("|", 1)
        except ValueError:
            # P0-7：游标错误从 CONTENT_003（敏感 422）拆分为独立码 CONTENT_008
            raise ApiError(ERR_CONTENT_008, "游标格式无效", http=422) from None
        # S1-M1 收口：统一走 sync_common.parse_ts（naive 视为 UTC；此前 fromisoformat
        # 裸调，非法游标会 500 且无 naive 处理）
        cursor_dt = parse_ts(cursor_ts_raw)
        if cursor_dt is None:
            raise ApiError(ERR_CONTENT_008, "游标格式无效", http=422)
        # 复合条件：(created_at, id) < (cursor_dt, cursor_id) 元组语义
        query = query.where(
            (Content.created_at < cursor_dt)
            | ((Content.created_at == cursor_dt) & (Content.id < cursor_id))
        )

    items = db.execute(query.limit(limit + 1)).scalars().all()
    has_more = len(items) > limit
    page_items = items[:limit]

    next_cursor = None
    if has_more and page_items:
        last = page_items[-1]
        # UTC 无 + 号格式，避免 URL 中 + 被解析为空格；拼接 id 兜底同秒
        ts_str = last.created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"
        next_cursor = f"{ts_str}|{last.id}"

    return ApiResponse(
        data=Page(
            items=[_attach_thumb(_to_out(c), c, str(user.id)) for c in page_items],
            cursor=next_cursor,
            has_more=has_more,
        )
    )


def _profile_sensitive_out(row: ProfileSensitive) -> ProfileSensitiveOut:
    return ProfileSensitiveOut(
        id=row.id,
        topic=row.topic,
        disposition=row.disposition,
        evidence=row.evidence or [],
        locked=row.locked,
        added_at=row.added_at,
        updated_at=row.updated_at,
    )


@profile_sensitive_router.post("/sensitive", response_model=ApiResponse[ProfileSensitiveOut])
def profile_sensitive_add(
    req: ProfileSensitiveCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """画像级敏感增/改（B1-6 对话式："别跟我提 X" → 记录话题+处置；幂等 upsert）"""
    from app.services.echo import upsert_profile_sensitive

    try:
        row = upsert_profile_sensitive(
            db, user.id, req.topic, req.disposition, req.evidence, req.locked
        )
    except ValueError as exc:
        raise ApiError(ERR_PROFILE_SENSITIVE_001, str(exc), http=422) from exc
    return ApiResponse(data=_profile_sensitive_out(row))


@profile_sensitive_router.delete("/sensitive", response_model=ApiResponse[ProfileSensitiveDeleteOut])
def profile_sensitive_delete(
    topic: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """画像级敏感删（B1-6）：DELETE /api/v1/profile/sensitive?topic=xxx"""
    from app.services.echo import delete_profile_sensitive

    if not topic.strip():
        raise ApiError(ERR_PROFILE_SENSITIVE_002, "topic 不能为空", http=422)
    deleted = delete_profile_sensitive(db, user.id, topic.strip())
    if not deleted:
        raise ApiError(ERR_PROFILE_SENSITIVE_003, f"话题不存在：{topic}", http=404)
    return ApiResponse(data={"deleted": True, "topic": topic.strip()})


@profile_sensitive_router.get("/sensitive", response_model=ApiResponse[list[ProfileSensitiveOut]])
def profile_sensitive_list(
    disposition: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """画像级敏感查（B1-6）：列出全部话题（可按处置级别过滤），按更新时间倒序"""
    from app.services.echo import list_profile_sensitive

    try:
        rows = list_profile_sensitive(db, user.id, disposition)
    except ValueError as exc:
        raise ApiError(ERR_PROFILE_SENSITIVE_001, str(exc), http=422) from exc
    return ApiResponse(data=[_profile_sensitive_out(r) for r in rows])


def _to_out(c: Content) -> ContentOut:
    return ContentOut(
        id=str(c.id),
        content_type=c.content_type,
        content_class=c.content_class,
        text=c.text,
        taken_at=c.taken_at,
        place=c.place,
        emotion=c.emotion,
        tags=[],
        status=c.status,
        audio_processing=(c.extra or {}).get("audio_processing"),
        created_at=c.created_at,
        # BA1（迁移 f2a3b4c5d6e7）：扩展字段直出（全可空，老数据为 None）
        duration=c.duration,
        remark=c.remark,
        size_bytes=c.size_bytes,
        tags_json=c.tags_json,
        ai_description=c.ai_description,
    )


def _attach_thumb(item: ContentOut, c: Content, user_id: str) -> ContentOut:
    """photo 条目补缩略图票（2026-09-05）：列表/搜索兜底卡片直连回显。
    票签发失败降级无图不阻塞列表（与 recall/favorites 同款容错）。"""
    if c.content_type == "photo" and c.thumbnail_key and item.thumbnail_url is None:
        try:
            _orig, thumb = content_urls(None, c.thumbnail_key, user_id)
            item.thumbnail_url = thumb
        except Exception:  # noqa: BLE001, S110 —— 票失败降级无图，不影响列表主链路（recall/favorites 同款容错）
            pass
    return item


# ---------- W2-1 删除/回收站 + W2-2 收藏（2026-09-05） ----------
# 设计要点：
# - 软删只置 deleted_at/deleted_by（全库既有查询已过滤 deleted_at，零侵入）
# - 收藏持久化走 contents.extra JSONB 键 favorite_at（零迁移；pipeline.set_extra 同款
#   「dict 重建再赋值」写法防 JSONB in-place mutation 不触发 UPDATE）
# - trash/favorites 独立 prefix router：/api/v1/trash、/api/v1/favorites 与
#   /api/v1/contents/{id} 路径段数不同零冲突（DELETE /{content_id} 只匹配 3 段）

from app.core.errors import ERR_CONTENT_010  # noqa: E402
from app.schemas.content import (  # noqa: E402
    TRASH_RETENTION_DAYS,
    ContentDeleteOut,
    ContentRemarkUpdate,
    FavoriteOut,
    TrashClearOut,
    TrashItemOut,
)
from app.services.external.media_url import content_urls  # noqa: E402

trash_router = make_router(prefix="/api/v1/trash", tags=["trash"])
favorites_router = make_router(prefix="/api/v1/favorites", tags=["favorites"])


def _load_alive_content(db: Session, user_id: str, content_id: str) -> Content:
    """取当前用户未删除内容；不存在/已删除/非本人 → 统一 404 CONTENT_010（IDOR 防护不区分三种情形）"""
    row = db.execute(
        select(Content).where(
            Content.id == content_id,
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(ERR_CONTENT_010, "内容不存在或无权访问", http=404)
    return row


@router.get("/{content_id}", response_model=ApiResponse[ContentOut])
def get_content_detail(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容详情（2026-09-09 缺口收口：客户端此前用 GET /contents?content_id= 列表过滤替代）

    归属/存在性：不存在 / 已软删 / 非本人 → 统一 404 CONTENT_010
    （复用 _load_alive_content，与 favorite/PATCH/waveform 同口径：IDOR 不区分三种情形；
    软删条目回收站走 GET /api/v1/trash）。畸形 ID 同语义 404（uuid4_str，R4#2）。
    路由顺序：本路由定义在 GET "" （列表，L367）之后，空串与路径参数不歧义；
    与 GET /{content_id}/waveform（两段路径）不冲突。
    出参：_to_out 全字段 + photo 条目补缩略图票（_attach_thumb，与列表同兜底）。
    """
    try:
        cid = uuid4_str(content_id)
    except NotFoundError:
        raise ApiError(ERR_CONTENT_010, "内容不存在或无权访问", http=404) from None
    row = _load_alive_content(db, user.id, cid)
    return ApiResponse(data=_attach_thumb(_to_out(row), row, str(user.id)))


@router.patch("/{content_id}", response_model=ApiResponse[ContentOut])
def update_content_remark(
    content_id: str,
    req: ContentRemarkUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """更新内容备注（BB1）：仅 remark 单字段（schema extra="forbid"，越权字段 422）。

    归属校验复用 _load_alive_content：非本人/已软删/不存在 → 404 CONTENT_010
    （IDOR 不区分三种情形）；畸形 ID 同语义 404（uuid4_str，R4#2）。
    """
    try:
        uuid4_str(content_id)
    except NotFoundError:
        raise ApiError(ERR_CONTENT_010, "内容不存在或无权访问", http=404) from None
    row = _load_alive_content(db, user.id, content_id)
    row.remark = req.remark
    db.commit()
    db.refresh(row)
    return ApiResponse(data=_to_out(row))


@router.delete("/{content_id}", response_model=ApiResponse[ContentDeleteOut])
def delete_content(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """软删内容（W2-1）：置 deleted_at/deleted_by，30 天保留期后可被清理任务彻底清除"""
    row = _load_alive_content(db, user.id, content_id)
    now = datetime.now(timezone.utc)
    row.deleted_at = now
    row.deleted_by = user.id
    db.commit()
    return ApiResponse(
        data=ContentDeleteOut(
            content_id=row.id,
            deleted=True,
            permanent_at=now + timedelta(days=TRASH_RETENTION_DAYS),
        )
    )


@router.post("/{content_id}/favorite", response_model=ApiResponse[FavoriteOut])
def favorite_add(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏内容（W2-2）：extra.favorite_at 落 ISO 时间戳；重复收藏幂等返回既有态"""
    row = _load_alive_content(db, user.id, content_id)
    extra = dict(row.extra or {})
    existing = extra.get("favorite_at")
    if existing is None:
        extra["favorite_at"] = datetime.now(timezone.utc).isoformat()
        row.extra = extra
        db.commit()
        existing = row.extra["favorite_at"]
    fav_dt = parse_ts(str(existing)) if existing else None
    return ApiResponse(data=FavoriteOut(content_id=row.id, favorite=True, favorite_at=fav_dt))


@router.delete("/{content_id}/favorite", response_model=ApiResponse[FavoriteOut])
def favorite_remove(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """取消收藏（W2-2）：extra.favorite_at 摘除；未收藏幂等返回 favorite=false"""
    row = _load_alive_content(db, user.id, content_id)
    extra = dict(row.extra or {})
    had = extra.pop("favorite_at", None)
    row.extra = extra
    if had is not None:
        db.commit()
    return ApiResponse(data=FavoriteOut(content_id=row.id, favorite=False, favorite_at=None))


@router.get("/{content_id}/favorite", response_model=ApiResponse[FavoriteOut])
def favorite_get(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询单条收藏态（W2-2：detail 页星标初始化用）"""
    row = _load_alive_content(db, user.id, content_id)
    fav_raw = (row.extra or {}).get("favorite_at")
    fav_dt = parse_ts(str(fav_raw)) if fav_raw else None
    return ApiResponse(data=FavoriteOut(content_id=row.id, favorite=fav_dt is not None, favorite_at=fav_dt))


@favorites_router.get("", response_model=ApiResponse[list[ContentOut]])
def favorites_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """收藏列表（W2-2）：extra.favorite_at 非空且未删除，按收藏时间倒序 + 缩略图签票"""
    rows = (
        db.execute(
            select(Content)
            .where(
                Content.user_id == user.id,
                Content.deleted_at.is_(None),
                Content.extra["favorite_at"].astext.isnot(None),
            )
            .order_by(Content.extra["favorite_at"].astext.desc())
        )
        .scalars()
        .all()
    )
    out: list[ContentOut] = []
    for c in rows:
        item = _to_out(c)
        if c.thumbnail_key:
            try:
                _orig, thumb = content_urls(None, c.thumbnail_key, str(user.id))
                item.thumbnail_url = thumb
            except Exception:  # 签票失败降级无图不阻塞主链路（recall.py 同口径）
                logging.getLogger(__name__).warning(
                    "收藏缩略图签票失败 content_id=%s", c.id, exc_info=True
                )
        out.append(item)
    return ApiResponse(data=out)


@trash_router.get("", response_model=ApiResponse[list[TrashItemOut]])
def trash_list(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站列表（W2-1）：软删条目按删除时间倒序，days_left 由后端算好"""
    now = datetime.now(timezone.utc)
    rows = (
        db.execute(
            select(Content)
            .where(Content.user_id == user.id, Content.deleted_at.is_not(None))
            .order_by(Content.deleted_at.desc())
        )
        .scalars()
        .all()
    )
    items: list[TrashItemOut] = []
    for c in rows:
        deleted = c.deleted_at
        if deleted is None:  # 理论不可达（查询已过滤）；防御
            continue
        if deleted.tzinfo is None:
            deleted = deleted.replace(tzinfo=timezone.utc)
        days_left = max(0, TRASH_RETENTION_DAYS - (now - deleted).days)
        items.append(
            TrashItemOut(
                id=str(c.id),
                content_type=c.content_type,
                text=c.text,
                place=c.place,
                taken_at=c.taken_at,
                deleted_at=deleted,
                days_left=days_left,
            )
        )
    return ApiResponse(data=items)


@trash_router.post("/{content_id}/restore", response_model=ApiResponse[ContentDeleteOut])
def trash_restore(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站恢复（W2-1）：清除 deleted_at/deleted_by，回到正常内容域"""
    row = db.execute(
        select(Content).where(
            Content.id == content_id,
            Content.user_id == user.id,
            Content.deleted_at.is_not(None),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(ERR_CONTENT_010, "回收站中不存在该内容或无权访问", http=404)
    row.deleted_at = None
    row.deleted_by = None
    db.commit()
    return ApiResponse(data=ContentDeleteOut(content_id=row.id, deleted=False, permanent_at=None))


@trash_router.delete("", response_model=ApiResponse[TrashClearOut])
def trash_clear(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """回收站清空（W2-1）：当前用户全部软删条目硬删（不可逆；客户端 showModal 强确认）。

    降级登记（_diff_ledger）：Qdrant 向量/COS 原件清理未挂接——MVP 只删 DB 行，
    向量残留不影响正确性（查询按 contents 行驱动）；挂接挂远期账 A3。
    """
    rows = (
        db.execute(
            select(Content).where(
                Content.user_id == user.id, Content.deleted_at.is_not(None)
            )
        )
        .scalars()
        .all()
    )
    cleared = 0
    for row in rows:
        db.delete(row)
        cleared += 1
    db.commit()
    return ApiResponse(data=TrashClearOut(cleared=cleared))


# ---------- BB3：真实音频波形（2026-09-08） ----------
# 只读端点：GET /api/v1/contents/{id}/waveform?buckets=N
# 客户端 VoiceWave 组件真实波形数据源（替代 genWaveHeights 确定性伪波形的升级通路）。
# 缓存取舍：语音条 ≤60s（16kHz/16bit/单声道 ≈1.9MB），解析为纯内存分桶峰值采样，
# 现算成本毫秒级，低于缓存失效/存储复杂度——故每次现算不缓存（量大后再议 LRU）。
_WAVEFORM_MAX_BYTES = 16 * 1024 * 1024  # 防御上限：60s 语音 ≈1.9MB，远超真实值才触发


def _wav_bucket_peaks(data: bytes, buckets: int) -> list[float]:
    """解析 WAV（RIFF/PCM16）→ buckets 个 0..1 峰值。

    容错：chunk 遍历找 data chunk（不认死 44 字节标准偏移，兼容扩展头/前缀附注块）。
    非 RIFF/WAVE、缺 fmt/data、非 PCM16、样本为空 → ValueError（调用方映射 404 显式报错）。
    """
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        raise ValueError("not RIFF/WAVE")
    audio_format: int | None = None
    bits: int | None = None
    pcm: bytes | None = None
    pos = 12
    while pos + 8 <= len(data):
        chunk_id = data[pos : pos + 4]
        (chunk_size,) = struct.unpack_from("<I", data, pos + 4)
        body = data[pos + 8 : pos + 8 + chunk_size]
        if chunk_id == b"fmt " and len(body) >= 16:
            audio_format, _ch, _rate, _br, _align, bits = struct.unpack_from("<HHIIHH", body)
        elif chunk_id == b"data":
            pcm = body
            break  # data 一般是最后的有效块（fmt 在前），提前止省一轮遍历
        pos += 8 + chunk_size + (chunk_size & 1)  # chunk 按 2 字节对齐
    if audio_format != 1 or bits != 16:
        raise ValueError("not PCM16")
    if not pcm:
        raise ValueError("empty data chunk")
    samples = array.array("h")
    usable = len(pcm) - len(pcm) % 2
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":
        samples.byteswap()
    total = len(samples)
    step = total / buckets
    peaks: list[float] = []
    for b in range(buckets):
        start = int(b * step)
        end = min(max(start + 1, int((b + 1) * step)), total)
        if start >= total:
            peaks.append(0.0)  # 样本数 < 桶数：尾部桶补 0（不静默截断桶数）
            continue
        peak = max(abs(v) for v in samples[start:end])
        peaks.append(peak / 32768.0)
    return peaks


@router.get("/{content_id}/waveform", response_model=ApiResponse[dict])
def get_content_waveform(
    content_id: str,
    buckets: int = Query(default=28, ge=1, le=128),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容音频真实波形（BB3 · 2026-09-08 新增）

    客户端 VoiceWave 组件数据源：读语音原件 → 解析 WAV data chunk →
    buckets 桶峰值（0..1 浮点，客户端映射到条高渲染）。

    - 归属校验：不存在/已删除/非本人 → 404 CONTENT_010（_load_alive_content 同款 IDOR 防护）
    - 非语音内容 / cos_key 缺失 / 对象读取失败 / 非 PCM16 WAV → 404 MEDIA_003
      「音频不可用」（与 media.get_content_audio 同口径：对外不区分原因防探测，
      服务端日志留真因；显式错误码非静默）
    - buckets 参数由 FastAPI Query 校验（1..128，越界自动 422 信封）
    - 不缓存：语音 ≤60s 现算毫秒级，见上方 BB3 注释
    """
    cid = uuid4_str(content_id)
    row = _load_alive_content(db, user.id, cid)
    if row.content_type != "voice" or not row.cos_key:
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404)
    try:
        data = get_storage_backend().get_object(row.cos_key)
    except (KeyError, ValueError) as exc:
        logger.info("波形音频对象不可用 key=%s err=%s", row.cos_key, exc)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    except StorageError as exc:
        logger.warning("波形音频读取失败（存储故障）key=%s code=%s", row.cos_key, exc.code)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    if len(data) > _WAVEFORM_MAX_BYTES:
        logger.warning("波形音频超防御上限 key=%s size=%d", row.cos_key, len(data))
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404)
    try:
        peaks = _wav_bucket_peaks(data, buckets)
    except ValueError as exc:
        logger.info("波形解析失败 content_id=%s reason=%s", cid, exc)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    return ApiResponse(data={"buckets": [round(p, 4) for p in peaks]})
