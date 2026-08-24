"""事件路由：四层事件模型（B3）+ 时间轴（F8）+ 用户手动操作（B3-5）"""

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.event import (
    EventConfirmRequest,
    EventItemOut,
    EventMergeRequest,
    EventOut,
    EventSplitRequest,
    EventSyncRequest,
    EventSyncResult,
)

router = APIRouter(prefix="/api/v1/events", tags=["events"])


@router.get("/timeline", response_model=ApiResponse[list[EventOut]])
def timeline(
    level: int | None = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """时间轴（F8）：L1 日卡片 + L2 主题事件 + L3 主题流；按 start_time 排序

    2026-08-20：事件聚合服务已接线（services/events.py），返回真实 events 数据；
    手动操作（merge/split/confirm）仍 501（用户操作优先原则，后续实现）。
    安全：需登录。
    """
    from app.services.events import get_timeline

    events = get_timeline(db, str(user.id), level=level)
    # 审查修复(P1-11)：一次 GROUP BY 批量取计数，消除 N+1（原逐事件 count 查询）
    counts = _batch_counts(db, [e.id for e in events])
    return ApiResponse(data=[_to_out(e, counts) for e in events])


def _batch_counts(db: Session, event_ids: list[str]) -> dict[str, dict]:
    """批量计数：{event_id: {content_count, photo_count}}（一次 GROUP BY 查询）"""
    from sqlalchemy import func

    from app.db.models import Content, EventItem

    if not event_ids:
        return {}
    rows = db.execute(
        select(
            EventItem.event_id,
            func.count().label("total"),
            func.count().filter(Content.content_type == "photo").label("photos"),
        )
        .join(Content, Content.id == EventItem.content_id)
        .where(EventItem.event_id.in_(event_ids))
        .group_by(EventItem.event_id)
    ).all()
    return {str(r.event_id): {"content_count": int(r.total), "photo_count": int(r.photos)} for r in rows}


def _to_out(e, counts: dict | None = None) -> EventOut:
    """Event ORM → EventOut（计数预取，审查 P1-11 修复 N+1）"""
    counts = counts or {}
    c = counts.get(str(e.id), {"content_count": 0, "photo_count": 0})
    return EventOut(
        id=str(e.id),
        level=e.level,
        title=e.title,
        title_source=e.title_source,
        cover_content_id=str(e.cover_content_id) if e.cover_content_id else None,
        start_time=e.start_time,
        end_time=e.end_time,
        place=e.place,
        emotion=e.emotion,
        sensitivity=e.sensitivity,
        confidence=e.confidence,
        status=e.status,
        generated_by=e.generated_by,
        content_count=c["content_count"],
        photo_count=c["photo_count"],
    )


@router.post("/sync", response_model=ApiResponse[EventSyncResult])
def sync_events(
    req: EventSyncRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """端侧 L1 事件批量提交（S-SY-1 · B3-6 端侧 L0/L1 真值）

    client_event_id 幂等（网络重试只落一次）；照片归属校验（越权拒绝）；
    落库后云侧只跑 L2/L3 候选（caption/CI 打标保留 _process_photo）。
    变更写入 offline_queue → 其他端增量拉取可见（M4 端间同步一致）。
    """
    from app.services.events import sync_client_events_safe

    result = sync_client_events_safe(
        db,
        str(user.id),
        req.device_id,
        [e.model_dump() for e in req.events],
    )
    return ApiResponse(data=EventSyncResult(**result))


@router.get("/{event_id}/items", response_model=ApiResponse[list[EventItemOut]])
def event_items(
    event_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """事件成员明细（2026-08-25 · split UI 前置）

    返回事件内内容列表（照片/文字/语音，title 可直接展示），
    客户端据此做选片拆分；归属校验（他人事件 404）。
    """
    from app.services.events import get_event_items as _items

    try:
        items = _items(db, str(user.id), event_id)
    except ValueError as exc:
        raise ApiError("EVENT_004", str(exc), http=404) from exc
    return ApiResponse(data=items)


@router.post("/merge", response_model=ApiResponse[EventOut])
def merge_events(req: EventMergeRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户合并（B3-5：存合并规则，算法永不覆盖用户决定，AGG-013）

    2026-08-20：手动操作已接线；source 内容并入 target，source 软删，target 置 confirmed。
    """
    from app.services.events import merge_events as _merge

    try:
        ev = _merge(db, str(user.id), req.target_event_id, req.source_event_ids)
    except ValueError as exc:
        raise ApiError("EVENT_004", str(exc), http=404) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))


@router.post("/split", response_model=ApiResponse[EventOut])
def split_event(req: EventSplitRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户拆分（B3-5）：拆出内容建独立事件；拆出的新事件置 confirmed"""
    from app.services.events import split_event as _split

    try:
        ev = _split(db, str(user.id), req.event_id, req.content_ids)
    except ValueError as exc:
        raise ApiError("EVENT_004", str(exc), http=404) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))


@router.post("/confirm", response_model=ApiResponse[EventOut])
def confirm_event(req: EventConfirmRequest, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """用户确认（置信度<0.7 转正；用户背书后不再被算法改动）"""
    from app.services.events import confirm_event as _confirm

    try:
        ev = _confirm(db, str(user.id), req.event_id, title=req.title)
    except ValueError as exc:
        raise ApiError("EVENT_004", str(exc), http=404) from exc
    return ApiResponse(data=_to_out(ev, _batch_counts(db, [ev.id])))
