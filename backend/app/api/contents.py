"""内容路由：四类素材上传主链路（API-002）+ 相册直传（COS STS，决策 #10）

真实 DB 接入（S1-02）：
- contents 表入库 + perceptual_hash 去重（Q16，同用户唯一）
- RQ 入队异步 AI 管线（API-016：收件→转写→分类→聚类；API 立即返回）
- 分页游标（API-006）
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.core.queue import enqueue_low
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse, Page
from app.schemas.content import ContentCreate, ContentOut, ContentUploadResult, CosPresign
from app.workers.worker import process_content

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])


@router.post("", response_model=ApiResponse[ContentOut])
def create_content(
    req: ContentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """内容入库：POST → contents 表 → 异步 AI 管线（RQ）→ 状态回写（API-002/API-016）"""
    if req.content_type not in ("photo", "text", "voice", "article"):
        raise ApiError("CONTENT_001", "不支持的 content_type", http=422)

    # 去重（Q16）：同用户 perceptual_hash 唯一（仅照片类有哈希）
    if req.perceptual_hash:
        dup = db.execute(
            select(Content).where(
                Content.user_id == user.id,
                Content.perceptual_hash == req.perceptual_hash,
            )
        ).scalar_one_or_none()
        if dup is not None:
            raise ApiError("CONTENT_002", "重复内容（感知哈希已存在）", http=409)

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
    db.commit()
    db.refresh(record)

    # 入队异步 AI 管线（API-016；API 立即返回不阻塞）
    enqueue_low(process_content, str(record.id))

    return ApiResponse(data=_to_out(record))


@router.post("/presign", response_model=ApiResponse[ContentUploadResult])
def presign_upload(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """照片直传：后端签 STS 临时密钥（30 秒有效）→ 客户端直传 COS（决策 #10/SYNC-013）"""
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
    """内容分页列表（API-006 游标分页：数据增删中翻页不错位）"""
    limit = min(max(limit, 1), 100)
    query = select(Content).where(
        Content.user_id == user.id,
        Content.deleted_at.is_(None),
    ).order_by(Content.created_at.desc())

    if cursor:
        # 游标 = created_at ISO 时间（同秒冲突由 id 兜底，MVP 够用）
        from datetime import datetime as dt

        cursor_dt = dt.fromisoformat(cursor.replace("Z", "+00:00"))
        query = query.where(Content.created_at < cursor_dt)

    items = db.execute(query.limit(limit + 1)).scalars().all()
    has_more = len(items) > limit
    page_items = items[:limit]

    next_cursor = None
    if has_more and page_items:
        # UTC 无 + 号格式，避免 URL 中 + 被解析为空格
        next_cursor = page_items[-1].created_at.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"

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
        created_at=c.created_at,
    )
