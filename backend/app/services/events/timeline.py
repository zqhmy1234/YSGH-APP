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

from sqlalchemy import and_, func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventItem
from app.services.event_aggregation.agg_candidates import l3_lifecycle
from app.services.pagination import decode_ts_cursor, encode_ts_cursor

logger = logging.getLogger("yishu.events")

__all__ = [
    "encode_timeline_cursor",
    "get_event_last_activity",
    "get_timeline",
    "get_timeline_page",
    "l3_lifecycle",
]


def _timeline_filters(
    user_id: str,
    level: int | None,
    status: str | None,
    pending: bool,
) -> list:
    """时间轴 WHERE 条件（**单一真源**：全量路径与分页路径共用，防两份条件漂移）

    2026-10-30（P2 B-7）：分页改造前这里只有一份内联条件；若分页版另写一份，
    「pending 口径」这类三处联动的判断迟早会在两条路径上分叉（同一用户看到
    全量页里有、分页页里没有的事件）。
    """
    clauses = [Event.user_id == user_id, Event.deleted_at.is_(None)]
    if level is not None:
        clauses.append(Event.level == level)
    if status is not None:
        clauses.append(Event.status == status)
    if pending:
        # Wave2-AgentD：L2 待确认区 = level>=2 且 draft 且 confidence<0.7
        clauses.extend(
            [
                Event.level >= 2,
                Event.status == "draft",
                or_(Event.confidence.is_(None), Event.confidence < 0.7),
            ]
        )
    return clauses


def get_timeline(
    db: Session,
    user_id: str,
    level: int | None = None,
    status: str | None = None,
    pending: bool = False,
) -> list[Event]:
    """时间轴（F8）：用户事件列表，按 start_time 倒序（**全量，不分页**）

    Wave2-AgentD：支持 L2 待确认区筛选——
      pending=True → level>=2 且 status=draft 且 confidence<0.7（B3-5 <0.7 进待确认）

    2026-10-30（P2 B-7）：本函数保持**原样全量语义**（老客户端/存量测试/统计口径
    都依赖它）；需要分页请用 `get_timeline_page`。
    """
    stmt = (
        select(Event)
        .where(*_timeline_filters(user_id, level, status, pending))
        .order_by(Event.start_time.desc())
    )
    return db.execute(stmt).scalars().all()


def encode_timeline_cursor(event: Event) -> str:
    """事件 → 不透明游标（格式与实现见 `app.services.pagination`）"""
    return encode_ts_cursor(event.start_time, str(event.id))


def get_timeline_page(
    db: Session,
    user_id: str,
    level: int | None = None,
    status: str | None = None,
    pending: bool = False,
    limit: int = 20,
    cursor: str | None = None,
) -> tuple[list[Event], bool]:
    """时间轴分页（P2 B-7 · 2026-10-30）：键集游标，返回 (本页事件, 是否还有更多)

    排序 `start_time DESC, id DESC`，与全量路径 `get_timeline` 的 `start_time DESC`
    **同一顺序的延伸**（PG 的 DESC 默认 NULLS FIRST，这里显式写出 `.nullsfirst()`，
    避免换库/换版本后 NULL 段跑到末尾让「携带 NULL 的页」重复或漏行）。

    NULL 段为什么必须特判（不是洁癖）：`events.start_time` 可空，NULL 段在 DESC 里
    排在**最前**，一旦游标落在 NULL 段内，`start_time < ts` 对 NULL 恒为假 ⇒ 后续
    NULL 行会被整体跳过（翻页"少了几张日卡"，且没有任何报错）。故 NULL 游标分支写成
    「仍在 NULL 段（id 更小）**或**已进入非 NULL 段」。

    Raises:
        ValueError: 游标格式非法（API 层映射 422；不静默当"没传游标"）。
    """
    stmt = select(Event).where(*_timeline_filters(user_id, level, status, pending))
    if cursor:
        cursor_ts, cursor_id = decode_ts_cursor(cursor)
        if cursor_ts is None:
            stmt = stmt.where(
                or_(
                    and_(Event.start_time.is_(None), Event.id < cursor_id),
                    Event.start_time.isnot(None),
                )
            )
        else:
            stmt = stmt.where(
                or_(
                    Event.start_time < cursor_ts,
                    and_(Event.start_time == cursor_ts, Event.id < cursor_id),
                )
            )
    stmt = (
        stmt.order_by(Event.start_time.desc().nullsfirst(), Event.id.desc())
        .limit(limit + 1)  # 多取一条判 has_more（不比全量多一次 count）
    )
    rows = db.execute(stmt).scalars().all()
    has_more = len(rows) > limit
    return rows[:limit], has_more


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
