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
import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.core.queue import enqueue_high, enqueue_low
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse, Page
from app.schemas.content import ContentCreate, ContentOut, ContentUploadResult, CosPresign
from app.services.pipeline import process_content

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

# 客户端第一波照片中转上传限制（B-BE-1）
MAX_PHOTO_BYTES = 20 * 1024 * 1024  # 单张 20MB
ALLOWED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
_PHOTO_SOURCES = ("app", "windows", "wechat", "import")


def _extract_exif_datetime(data: bytes) -> datetime | None:
    """从照片字节提取 EXIF DateTimeOriginal（相机拍摄时间真值）

    客户端 DATE_TAKEN 可能被 MediaProvider 写成扫描时间（2026-08-24 真机实测），
    故以后端 EXIF 解析为准：EXIF 无时区=相机本地时间（本设备 +08），
    显式按 UTC+08:00 解释，与客户端 isoString(+08:00) 一致。
    """
    try:
        from PIL import Image

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
    # 1. meta JSON 解析
    try:
        meta_obj = json.loads(meta) if meta.strip() else {}
        if not isinstance(meta_obj, dict):
            raise ValueError("meta 必须是 JSON 对象")
    except (json.JSONDecodeError, ValueError) as exc:
        raise ApiError("CONTENT_005", "meta 必须为合法 JSON 对象", http=422) from exc

    # 2. 文件校验（类型白名单 + 非空 + 大小上限）
    ext = Path(file.filename or "").suffix.lower()
    content_type = (file.content_type or "").lower()
    if ext not in ALLOWED_PHOTO_EXTS and not content_type.startswith("image/"):
        raise ApiError("CONTENT_006", "仅支持照片文件（jpg/png/webp/heic）", http=422)
    data = file.file.read()
    if not data:
        raise ApiError("CONTENT_006", "文件为空", http=422)
    if len(data) > MAX_PHOTO_BYTES:
        raise ApiError(
            "CONTENT_007",
            f"照片超过大小上限（{MAX_PHOTO_BYTES // 1024 // 1024}MB）",
            http=413,
        )

    # 3. 元数据字段解析与边界校验
    taken_at = None
    if meta_obj.get("taken_at"):
        try:
            taken_at = datetime.fromisoformat(str(meta_obj["taken_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ApiError("CONTENT_005", "taken_at 格式无效（ISO8601）", http=422) from exc
    gps_lat = meta_obj.get("gps_lat")
    gps_lng = meta_obj.get("gps_lng")
    try:
        gps_lat = float(gps_lat) if gps_lat is not None else None
        gps_lng = float(gps_lng) if gps_lng is not None else None
    except (TypeError, ValueError) as exc:
        raise ApiError("CONTENT_005", "gps_lat/gps_lng 必须为数值", http=422) from exc
    if gps_lat is not None and not (-90 <= gps_lat <= 90):
        raise ApiError("CONTENT_005", "gps_lat 越界（-90~90）", http=422)
    if gps_lng is not None and not (-180 <= gps_lng <= 180):
        raise ApiError("CONTENT_005", "gps_lng 越界（-180~180）", http=422)
    source = meta_obj.get("source", "app")
    if source not in _PHOTO_SOURCES:
        raise ApiError("CONTENT_005", f"source 非法（可选 {_PHOTO_SOURCES}）", http=422)
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
            raise ApiError("CONTENT_002", "重复内容（感知哈希已存在）", http=409)

    # 5. 护栏（B5b）：meta.text 若提供则复用 moderate（照片本体由管线 _process_photo 检测）
    from app.services.external.dashscope import moderate

    check_text = (meta_obj.get("text") or "").strip()
    if check_text:
        verdict = moderate(check_text)
        if verdict.get("action") == "reject":
            raise ApiError("CONTENT_003", f"内容含敏感信息未保存：{verdict.get('reason', '')}", http=422)

    # 5.1 EXIF 拍摄时间优先（相机真值；客户端时间可能被扫描污染）
    exif_taken = _extract_exif_datetime(data)
    if exif_taken is not None:
        taken_at = exif_taken

    # 6. 原件落 storage（cos_key），随后建 contents 记录 + 入队
    from app.services.external.storage import get_storage_backend

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
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError("CONTENT_002", "重复内容（感知哈希已存在）", http=409) from None
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
        raise ApiError("CONTENT_001", "不支持的 content_type", http=422)

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
            raise ApiError("CONTENT_002", "重复内容（感知哈希已存在）", http=409)

    # 护栏检测（B5b · 2026-08-20 接入普通入库）：reject → 拒绝入库；mask → 打码后入库
    from app.services.external.dashscope import moderate

    check_text = req.text or ""
    if check_text.strip():
        verdict = moderate(check_text)
        if verdict.get("action") == "reject":
            raise ApiError("CONTENT_003", f"内容含敏感信息未保存：{verdict.get('reason', '')}", http=422)
        if verdict.get("action") == "mask" and verdict.get("masked_text"):
            req.text = verdict["masked_text"]

    record = Content(
        user_id=user.id,
        content_type=req.content_type,
        text=req.text,
        taken_at=req.taken_at,
        gps_lat=req.gps_lat,
        gps_lng=req.gps_lng,
        perceptual_hash=req.perceptual_hash,
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
        # 审查修复(P1-04)：并发同哈希上传 → 唯一约束冲突 → 回滚重查，返回 409
        db.rollback()
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == req.perceptual_hash,
                Content.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError("CONTENT_002", "重复内容（感知哈希已存在）", http=409) from None
        raise
    db.refresh(record)

    # 入队异步 AI 管线（API-016；API 立即返回不阻塞）
    # 审查修复(P1-12)：voice/photo 用户等待 → 高优队列；text/article 低优
    if req.content_type in ("voice", "photo"):
        enqueue_high(process_content, str(record.id))
    else:
        enqueue_low(process_content, str(record.id))

    return ApiResponse(data=_to_out(record))


@router.post("/presign", response_model=ApiResponse[ContentUploadResult])
def presign_upload(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """照片直传：后端签 STS 临时密钥（30 秒有效）→ 客户端直传 COS（决策 #10/SYNC-013）

    审查修复(P1-03)：当前为 mock 实现（TODO T1），生产环境不得返回假凭证——
    客户端拿到 mock-secret-id 直传必然失败。生产未实现 → 501 显式告知。
    """
    from app.core.config import settings as _settings

    if _settings.app_env == "production" and not _settings.mock_external_ai:
        raise ApiError("CONTENT_004", "STS 直传未接入（生产待实现），请走后端中转上传", http=501)
    # TODO(T1): 腾讯云 STS 接口；M1 验证 STS 最短有效期限制（当前 mock）
    expire = datetime.now(timezone.utc) + timedelta(seconds=30)
    return ApiResponse(
        data=ContentUploadResult(
            content_id="mock-content-presign",
            status="ready",
            cos_presign=CosPresign(
                tmp_secret_id="mock-secret-id",
                tmp_secret_key="mock-secret-key",
                session_token="mock-session-token",
                expired_at=expire,
                cos_key=f"photos/{user.id}/{expire.timestamp():.0f}.jpg",
            ),
        )
    )


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
        from datetime import datetime as dt

        try:
            cursor_ts_raw, cursor_id = cursor.split("|", 1)
        except ValueError:
            raise ApiError("CONTENT_003", "游标格式无效", http=422) from None
        cursor_dt = dt.fromisoformat(cursor_ts_raw.replace("Z", "+00:00"))
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
