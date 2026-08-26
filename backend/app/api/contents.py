"""内容路由：四类素材上传主链路（API-002）+ 相册直传（COS STS，决策 #10）

真实 DB 接入（S1-02）：
- contents 表入库 + perceptual_hash 去重（Q16，同用户唯一）
- RQ 入队异步 AI 管线（API-016：收件→转写→分类→聚类；API 立即返回）
- 分页游标（API-006）

客户端第一波（2026-08-24，B-BE-1/2）：
- POST /api/v1/contents/upload：照片 multipart 中转上传（客户端→后端→storage→contents→管线）
- 复用 create_content 的去重（409）/护栏（moderate）/类型白名单语义
"""
import io as _io
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import (
    ERR_CONTENT_001,
    ERR_CONTENT_002,
    ERR_CONTENT_003,
    ERR_CONTENT_005,
    ERR_CONTENT_006,
    ERR_CONTENT_007,
    ERR_CONTENT_008,
    ERR_CONTENT_009,
    ERR_PROFILE_SENSITIVE_001,
    ERR_PROFILE_SENSITIVE_002,
    ERR_PROFILE_SENSITIVE_003,
    ApiError,
)
from app.core.queue import enqueue_high, enqueue_low
from app.db.models import Content, ProfileSensitive, User
from app.db.session import get_db
from app.schemas.common import ApiResponse, Page
from app.schemas.content import (
    ContentCreate,
    ContentOut,
    ProfileSensitiveCreate,
    ProfileSensitiveOut,
)
from app.services.file_magic import is_photo_bytes
from app.services.pipeline import process_content
from app.services.sync_common import parse_ts
from app.services.upload_meta import (
    ALLOWED_PHOTO_EXTS,
    MAX_PHOTO_BYTES,
    MetaValidationError,
    parse_photo_meta,
)

logger = logging.getLogger("yishu.contents")

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

# 画像级敏感对话式增删查（B1-6 / B5b FIX-4）：独立 router（prefix /api/v1/profile），
# 需集成 Agent 在 main.py 注册：app.include_router(profile_sensitive_router)。
profile_sensitive_router = APIRouter(prefix="/api/v1/profile", tags=["profile-sensitive"])

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
    source 白名单；照片原件落 storage（cos_key），随后 enqueue_high(process_content)。
    """
    # 1+3. meta 解析与字段校验（TD-P2B · S1-H2 收口：与分片链路 register_photo_content
    #      同一契约——taken_at ISO/gps 边界/source 白名单统一走 upload_meta.parse_photo_meta；
    #      字段契约错误优先返回 CONTENT_005）
    try:
        photo_meta = parse_photo_meta(meta)
    except MetaValidationError as exc:
        raise ApiError(ERR_CONTENT_005, str(exc), http=422) from exc
    meta_obj = photo_meta.raw
    taken_at = photo_meta.taken_at
    gps_lat = photo_meta.gps_lat
    gps_lng = photo_meta.gps_lng
    source = photo_meta.source

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

    # 4. 去重（Q16）：同用户 perceptual_hash 唯一（含软删过滤，语义与 create_content 一致）
    if perceptual_hash:
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError(ERR_CONTENT_002, "重复内容（感知哈希已存在）", http=409)

    # 5. 护栏（B5b）：meta.text 若提供则复用 moderate（照片本体由管线 _process_photo 检测）
    from app.services.external.dashscope import moderate

    check_text = (meta_obj.get("text") or "").strip()
    if check_text:
        verdict = moderate(check_text)
        if verdict.get("action") == "reject":
            _reflow_violation(db, verdict)
            raise ApiError(ERR_CONTENT_003, f"内容含敏感信息未保存：{verdict.get('reason', '')}", http=422)

    # 5.1 EXIF 拍摄时间优先（相机真值；客户端时间可能被扫描污染）
    exif_taken = _extract_exif_datetime(data)
    if exif_taken is not None:
        taken_at = exif_taken

    # 6. 原件落 storage（cos_key），随后建 contents 记录 + 入队
    from app.services.external.storage import best_effort_delete, get_storage_backend

    ext_safe = ext if ext in ALLOWED_PHOTO_EXTS else ".jpg"
    cos_key = f"photos/{user.id}/{uuid.uuid4().hex}{ext_safe}"
    get_storage_backend().put_object(cos_key, data)

    record = Content(
        user_id=user.id,
        content_type="photo",
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        perceptual_hash=perceptual_hash,
        cos_key=cos_key,
        extra=extra,
        source=source,
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
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError(ERR_CONTENT_002, "重复内容（感知哈希已存在）", http=409) from None
        raise
    db.refresh(record)

    enqueue_high(process_content, str(record.id))
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
            _reflow_violation(db, verdict)
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
    if req.content_type in ("voice", "photo"):
        enqueue_high(process_content, str(record.id))
    else:
        enqueue_low(process_content, str(record.id))

    return ApiResponse(data=_to_out(record))


@router.get("", response_model=ApiResponse[Page[ContentOut]])
def list_contents(
    limit: int = 20,
    cursor: str | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容分页列表（API-006 游标分页：数据增删中翻页不错位）

    修复（审查 MAJOR）：原游标仅 created_at，同秒多条翻页错位/重复；
    改为 (created_at, id) 复合游标（id 兜底，UUID 不可比 → 用字符串序）。
    游标格式："<created_at_iso>|<id>"。
    """
    limit = min(max(limit, 1), 100)
    query = select(Content).where(
        Content.user_id == user.id,
        Content.deleted_at.is_(None),
    ).order_by(Content.created_at.desc(), Content.id.desc())

    if cursor:
        try:
            cursor_ts_raw, cursor_id = cursor.split("|", 1)
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
            items=[_to_out(c) for c in page_items],
            cursor=next_cursor,
            has_more=has_more,
        )
    )


def _reflow_violation(db: Session, verdict: dict) -> None:
    """违规词回流（B5b）：moderate 命中（reject）→ 命中词写 SensitiveWord(level=3)
    自动入规则表（幂等；失败仅记录，不阻断用户请求）"""
    try:
        from app.services.llm_ops.guard import reflow_violation_words

        reflow_violation_words(db, verdict.get("matched") or [])
    except Exception:  # noqa: BLE001
        logger.warning("违规词回流失败（不阻断请求）", exc_info=True)


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


@profile_sensitive_router.delete("/sensitive", response_model=ApiResponse)
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
    )
