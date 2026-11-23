"""Conversation history: one read path, paginated from newest backwards.

Split from ``chat.py`` to keep the route module under the filesize ratchet.
The response order stays chronological; only the query direction is newest-first.
"""

from __future__ import annotations

from fastapi import Depends, Query
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.chat_helpers import _resolve_conversation_id
from app.api.deps import get_current_user
from app.core.errors import ERR_CHAT_005, ApiError
from app.db.models import ChatMessage, User
from app.db.session import get_db
from app.schemas.chat import ChatHistoryItemOut, ChatHistoryOut
from app.schemas.common import ApiResponse
from app.services.pagination import decode_ts_cursor, encode_ts_cursor

router = make_router(prefix="/api/v1/chat", tags=["chat"])
_HISTORY_DEFAULT_LIMIT = 20


@router.get("/conversations/{conversation_id}/messages", response_model=ApiResponse[ChatHistoryOut])
def get_history(
    conversation_id: str,
    limit: int | None = Query(
        None,
        ge=1,
        le=200,
        description="每页条数（1-200，返回**最近** N 条）。省略且不带 cursor = 全量（向后兼容）",
    ),
    cursor: str | None = Query(
        None,
        description="取更早一页的游标（取上次响应 data.cursor 原样回传，勿自行拼装）",
    ),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取本会话历史（时间正序）——客户端进入会话时回填，供“问 AI 带上下文”。

    ## 2026-10-30（P2 D-11）分页：为什么“从最近往回翻”

    改造前无条件取全会话（`order_by(created_at.asc())` 无 limit）。会话越长，进入对话页
    的等待越久；而用户进入时真正要看的是**最后几轮**，几十轮前的历史在首屏之外。
    故分页语义定为「先给最近 limit 条，再按 cursor 往前翻」：
      · SQL 侧 `created_at DESC, id DESC` 取 limit+1（多取一条判 has_more）；
      · **出参仍按时间正序**（客户端渲染顺序不变，改造前的逐条顺序不被打乱）；
      · cursor = 本页**最早**那条，语义是“比它更早”。
    不传 limit/cursor 时全量（老客户端与既有测试的行为原样保留）。
    游标非法 → 422 CHAT_005（不静默退化成第一页：那会让用户以为翻到了更早的消息）。
    """
    normalized = _resolve_conversation_id(db, str(user.id), conversation_id)
    paged = limit is not None or cursor is not None
    stmt = select(ChatMessage).where(
        ChatMessage.user_id == str(user.id), ChatMessage.conversation_id == normalized
    )
    if cursor:
        try:
            cursor_ts, cursor_id = decode_ts_cursor(cursor)
        except ValueError as exc:
            raise ApiError(ERR_CHAT_005, f"游标格式无效：{exc}", http=422) from exc
        if cursor_ts is None:
            raise ApiError(ERR_CHAT_005, "游标格式无效：时间位不可为空", http=422)
        stmt = stmt.where(
            or_(
                ChatMessage.created_at < cursor_ts,
                and_(ChatMessage.created_at == cursor_ts, ChatMessage.id < cursor_id),
            )
        )

    if paged:
        effective_limit = limit if limit is not None else _HISTORY_DEFAULT_LIMIT
        rows_desc = db.execute(
            stmt.order_by(ChatMessage.created_at.desc(), ChatMessage.id.desc()).limit(
                effective_limit + 1
            )
        ).scalars().all()
        has_more = len(rows_desc) > effective_limit
        rows = list(reversed(rows_desc[:effective_limit]))
        next_cursor = (
            encode_ts_cursor(rows[0].created_at, str(rows[0].id)) if (has_more and rows) else None
        )
    else:
        effective_limit = None
        rows = db.execute(stmt.order_by(ChatMessage.created_at.asc())).scalars().all()
        has_more = False
        next_cursor = None

    return ApiResponse(
        data=ChatHistoryOut(
            conversation_id=normalized,
            messages=[
                ChatHistoryItemOut(
                    id=str(r.id),
                    role=r.role,
                    kind=r.kind,
                    text=r.content,
                    created_at=r.created_at.isoformat(),
                    chips=list((r.payload or {}).get("chips") or []),
                    cards=list((r.payload or {}).get("cards") or []),
                )
                for r in rows
            ],
            cursor=next_cursor,
            limit=effective_limit,
            has_more=has_more,
        )
    )


__all__ = ["router"]
