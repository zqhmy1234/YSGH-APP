"""照片→事件反向入口（B3-4 · 2026-08-26 Wave2 AgentE 新建）

照片详情页"属于：事件列表"：给定 content_id，返回该内容所属的全部事件
（L0/L1/L2/L3 多对多——一张照片可同时属于 L0 瞬间 + L3 主题流）。

设计约束（并行开发）：
- 新文件零冲突：不触碰 api/events.py / services/events.py（Agent D 独占），
  只读查询模型（models/migrations 只读，无新表/新列需求）。
- 归属校验：内容不存在或非本人 → 404（与 get_event_items 语义一致）。
- 照片计数一次 GROUP BY 批量取，避免 N+1（与 api/events.py _batch_counts 同法）。

端点：GET /api/v1/contents/{content_id}/events
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, uuid4_str
from app.core.errors import ERR_EVENT_005, ApiError
from app.db.models import Content, Event, EventItem, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.services.errors import NotFoundError

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])


class ContentEventOut(BaseModel):
    """照片所属事件（反向入口输出：事件列表 + 标题 + 跳转所需字段）"""

    id: str
    level: int = Field(..., ge=0, le=3)
    title: str | None = None
    title_source: str | None = None      # llm / template / user
    cover_content_id: str | None = None  # 封面（客户端首图回退）
    start_time: datetime | None = None
    end_time: datetime | None = None
    place: str | None = None
    status: str = "draft"                # draft / confirmed / rejected
    confidence: float | None = None
    photo_count: int = 0


def get_content_events(db: Session, user_id: str, content_id: str) -> list[dict]:
    """内容所属事件列表（按 start_time 倒序；内容不存在/非本人 → NotFoundError（ValueError 兼容））

    - 只查未删除事件（软删 30 天规则：删除的事件不参与展示）
    - 照片↔事件多对多：同一内容可属多个事件，全部返回
    """
    cid = uuid4_str(content_id)
    content = db.execute(
        select(Content).where(Content.id == cid, Content.deleted_at.is_(None))
    ).scalar_one_or_none()
    if content is None or str(content.user_id) != user_id:
        raise NotFoundError(f"内容不存在或不属于当前用户: {content_id}")

    rows = db.execute(
        select(Event)
        .join(EventItem, EventItem.event_id == Event.id)
        .where(
            EventItem.content_id == cid,
            Event.user_id == user_id,
            Event.deleted_at.is_(None),
        )
        .order_by(Event.start_time.desc().nulls_last(), Event.created_at.desc())
    ).scalars().all()

    # 批量照片计数（一次 GROUP BY，审查 P1-11 同款防 N+1）
    counts: dict[str, int] = {}
    if rows:
        cnt_rows = db.execute(
            select(
                EventItem.event_id,
                func.count().filter(Content.content_type == "photo").label("photos"),
            )
            .join(Content, Content.id == EventItem.content_id)
            .where(EventItem.event_id.in_([e.id for e in rows]))
            .group_by(EventItem.event_id)
        ).all()
        counts = {str(r.event_id): int(r.photos) for r in cnt_rows}

    return [
        {
            "id": str(e.id),
            "level": e.level,
            "title": e.title,
            "title_source": e.title_source,
            "cover_content_id": str(e.cover_content_id) if e.cover_content_id else None,
            "start_time": e.start_time,
            "end_time": e.end_time,
            "place": e.place,
            "status": e.status,
            "confidence": e.confidence,
            "photo_count": counts.get(str(e.id), 0),
        }
        for e in rows
    ]


@router.get("/{content_id}/events", response_model=ApiResponse[list[ContentEventOut]])
def content_events(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """照片→事件反向查询（B3-4 照片详情"属于"列表）

    返回该照片所属的事件列表（含标题/层级/封面/时间窗），客户端据此展示
    "属于：事件1 · 事件2"并可跳转；空数组 = 照片尚未归属任何事件。
    安全：需登录；他人内容 404。
    """
    try:
        events = get_content_events(db, str(user.id), content_id)
    except ValueError as exc:
        # P0-7：内容不存在从 CONTENT_007（413 超限语义）拆分为 EVENT_005（404）
        raise ApiError(ERR_EVENT_005, str(exc), http=404) from exc
    return ApiResponse(data=events)
