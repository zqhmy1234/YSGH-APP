"""时间轴（F5/R1#5 拆包：services/events.py → services/events/timeline.py）

职责：用户事件列表（F8 时间轴）+ 事件最近活动时间（L3 生命周期读取时派生）。

B9a（2026-09-24 重构波）：**L3 生命周期衍生的对外门面**。
`l3_lifecycle` 的实现在 `event_aggregation/agg_candidates.py`（算法包），但它的
**消费方是 API 层**（`api/events.py` 读取时派生 L3 状态）。此前 API 直接
`from app.services.event_aggregation.pipeline import l3_lifecycle` ⇒ **API 越级直连
算法包**（跨层反向依赖，FF1 关注面）。现由本模块（事件域、职责本就含"L3 生命周期
派生"）**再导出**，API 只依赖 `app.services.events` 门面。
依赖方向：events 域 → 算法包（同域内 service→algorithm，方向正确）；算法包**不**反向依赖本模块。
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventItem
from app.services.event_aggregation.agg_candidates import l3_lifecycle

logger = logging.getLogger("yishu.events")

__all__ = ["get_event_last_activity", "get_timeline", "l3_lifecycle"]


def get_timeline(
    db: Session,
    user_id: str,
    level: int | None = None,
    status: str | None = None,
    pending: bool = False,
) -> list[Event]:
    """时间轴（F8）：用户事件列表，按 start_time 倒序

    Wave2-AgentD：支持 L2 待确认区筛选——
      pending=True → level>=2 且 status=draft 且 confidence<0.7（B3-5 <0.7 进待确认）
    """
    stmt = (
        select(Event)
        .where(Event.user_id == user_id, Event.deleted_at.is_(None))
        .order_by(Event.start_time.desc())
    )
    if level is not None:
        stmt = stmt.where(Event.level == level)
    if status is not None:
        stmt = stmt.where(Event.status == status)
    if pending:
        stmt = stmt.where(
            Event.level >= 2,
            Event.status == "draft",
            or_(Event.confidence.is_(None), Event.confidence < 0.7),
        )
    return db.execute(stmt).scalars().all()


def get_event_last_activity(db: Session, user_id: str, event_ids: list[str]) -> dict[str, datetime]:
    """批量取事件最近活动时间（成员 taken_at 最大值；无成员回退 start_time）

    供 L3 生命周期状态机（活跃→静默→归档）在读取时派生（MVP 不落库）。
    """
    if not event_ids:
        return {}
    rows = db.execute(
        select(EventItem.event_id, func.max(Content.taken_at))
        .join(Content, Content.id == EventItem.content_id)
        .where(EventItem.event_id.in_(event_ids))
        .group_by(EventItem.event_id)
    ).all()
    return {str(r[0]): r[1] for r in rows}
