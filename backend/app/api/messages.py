"""消息中心 API（S4-08 · 关怀追问 in-app / 复盘 push 记录）

GET  /api/v1/messages             —— 消息列表（分页 + status 过滤）
POST /api/v1/messages/{id}/read   —— 单条已读
POST /api/v1/messages/read-all    —— 全部已读（不打断：列表页退出即读）
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import Message, User
from app.db.session import get_db
from app.schemas.common import ApiResponse, Page
from app.schemas.message import MessageOut

router = APIRouter(prefix="/api/v1/messages", tags=["messages"])


def _to_out(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        channel=m.channel,
        msg_type=m.msg_type,
        title=m.title,
        body=m.body,
        payload=m.payload or {},
        status=m.status,
        sent_at=m.sent_at,
        read_at=m.read_at,
    )


@router.get("", response_model=ApiResponse[Page[MessageOut]])
def list_messages(
    status: str | None = None,
    limit: int = 20,
    cursor: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """消息列表（sent_at DESC 游标分页；status 过滤 unread/read/archived）"""
    limit = min(max(limit, 1), 50)
    query = select(Message).where(Message.user_id == user.id)
    if status:
        if status not in ("unread", "read", "archived"):
            raise ApiError("MSG_001", "status 仅支持 unread/read/archived", http=422)
        query = query.where(Message.status == status)
    if cursor:
        query = query.where(Message.id < cursor)
    query = query.order_by(Message.id.desc()).limit(limit + 1)

    items = db.execute(query).scalars().all()
    has_more = len(items) > limit
    page_items = items[:limit]
    return ApiResponse(
        data=Page(
            items=[_to_out(m) for m in page_items],
            cursor=str(page_items[-1].id) if has_more and page_items else None,
            has_more=has_more,
        )
    )


@router.post("/{msg_id}/read", response_model=ApiResponse)
def mark_read(
    msg_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """单条已读（越权访问他人消息 → 404 不泄露存在性）"""
    row = db.execute(
        update(Message)
        .where(Message.id == msg_id, Message.user_id == user.id, Message.status == "unread")
        .values(status="read", read_at=func.now())
    )
    if row.rowcount == 0:
        # 已是 read 也算成功（幂等）；非本人消息才 404
        exists = db.execute(
            select(func.count()).select_from(Message).where(
                Message.id == msg_id, Message.user_id == user.id
            )
        ).scalar()
        if not exists:
            raise ApiError("MSG_002", "消息不存在", http=404)
    db.commit()
    return ApiResponse(data={"read": msg_id})


@router.post("/read-all", response_model=ApiResponse)
def mark_all_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """全部已读（消息中心列表页退出即读，不打断）"""
    db.execute(
        update(Message)
        .where(Message.user_id == user.id, Message.status == "unread")
        .values(status="read", read_at=func.now())
    )
    db.commit()
    return ApiResponse(data={"read_all": True})
