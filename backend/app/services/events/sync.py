"""事件上云与拉取（F5/R1#5 拆包：services/events.py → services/events/sync.py）

职责：端侧 L1 事件批量提交（S-SY-1 · B3-6 端侧 L0/L1 真值）+ 变更日志
（offline_queue 为其他端增量拉取源 → M4 端间一致）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventItem, OfflineQueue
from app.services.events.aggregate import _refresh_upper_candidates

logger = logging.getLogger("yishu.events")


def sync_client_events(
    db: Session,
    user_id: str,
    device_id: str,
    events: list[dict],
) -> dict:
    """端侧 L1 事件批量提交（S-SY-1 · B3-6 端侧 L0/L1 真值）

    - 幂等：client_event_id（同用户）已存在 → duplicates，不重复落库（网络重试只落一次）
    - 归属校验：photo_ids 必须存在且属于当前用户；非法 → 整条 rejected
    - 落库：L1 事件（generated_by="device"）+ event_items + 变更日志（供其他端增量拉取）
    - 云侧只跑 L2/L3：受影响照片重算候选（caption/CI 打标在 _process_photo）
    """
    accepted: list[dict] = []
    duplicates: list[str] = []
    rejected: list[dict] = []
    affected: list[str] = []

    # S6-2 批量预取（N+1 → 每批各 1 次查询）：
    # ① 幂等键 client_event_id IN (...) 一次查
    # ② photo_ids 归属按批合并（一次 IN 查询覆盖全部事件的照片）
    cid_values = [item.get("client_event_id") for item in events]
    truthy_cids = [c for c in cid_values if c]
    existing_cids: set[str] = set()
    if truthy_cids:
        existing_cids = set(
            db.execute(
                select(Event.client_event_id).where(
                    Event.user_id == user_id,
                    Event.client_event_id.in_(truthy_cids),
                )
            ).scalars().all()
        )
    all_photo_ids = {str(p) for item in events for p in (item.get("photo_ids") or [])}
    owned_ids: set[str] = set()
    if all_photo_ids:
        owned_ids = {
            str(r) for r in db.execute(
                select(Content.id).where(
                    Content.id.in_(all_photo_ids),
                    Content.user_id == user_id,
                    Content.deleted_at.is_(None),
                )
            ).scalars().all()
        }

    for item in events:
        cid = item.get("client_event_id")
        if cid in existing_cids or (cid is None and db.execute(
            select(Event.id).where(
                Event.user_id == user_id, Event.client_event_id.is_(None)
            )
        ).scalar_one_or_none() is not None):
            duplicates.append(cid)
            continue

        photo_ids = [str(p) for p in (item.get("photo_ids") or [])]
        if not photo_ids:
            rejected.append({"client_event_id": cid, "reason": "photo_ids 为空"})
            continue
        valid = [p for p in photo_ids if p in owned_ids]
        invalid = [p for p in photo_ids if p not in owned_ids]
        if invalid:
            rejected.append(
                {"client_event_id": cid, "reason": f"照片不存在或不属于当前用户: {invalid[:5]}"}
            )
            continue

        start_ts = item.get("start_time")
        if isinstance(start_ts, str):
            start_ts = datetime.fromisoformat(start_ts)
        if start_ts is not None and start_ts.tzinfo is None:
            start_ts = start_ts.replace(tzinfo=timezone.utc)  # 客户端未带时区 → 按 UTC 归一
        end_ts = item.get("end_time")
        if isinstance(end_ts, str):
            end_ts = datetime.fromisoformat(end_ts)
        day_key = start_ts.astimezone().strftime("%Y-%m-%d") if start_ts else ""
        ev = Event(
            user_id=user_id,
            level=1,
            title=item.get("title") or (f"{day_key} · {len(valid)}条" if day_key else f"{len(valid)}条"),
            title_source="user" if item.get("title") else "template",
            start_time=start_ts,
            end_time=end_ts,
            place=item.get("place"),
            confidence=0.9,
            status="draft",
            generated_by="device",
            client_event_id=cid,
        )
        db.add(ev)
        db.flush()
        for pid in valid:
            db.add(EventItem(content_id=pid, event_id=ev.id))
        # 变更日志（B4：offline_queue 为增量拉取源，其他端可拉到该事件 → M4 端间一致）
        db.add(
            OfflineQueue(
                op_id=f"ev-{cid}",
                user_id=user_id,
                device_id=device_id,
                op_type="upsert_event",
                payload={
                    "op_type": "upsert_event",
                    "entity_type": "event",
                    "entity_id": str(ev.id),
                    "field": None,
                    "value": {
                        "client_event_id": cid,
                        "title": ev.title,
                        "title_source": ev.title_source,
                        "start_time": ev.start_time.isoformat() if ev.start_time else None,
                        "end_time": ev.end_time.isoformat() if ev.end_time else None,
                        "place": ev.place,
                        "photo_ids": list(valid),
                    },
                    "updated_at": ev.created_at.isoformat() if ev.created_at else None,
                },
                status="done",
            )
        )
        accepted.append({"client_event_id": cid, "event_id": str(ev.id), "photo_count": len(valid)})
        affected.extend(valid)

    upper_items = 0
    if affected:
        upper_items = _refresh_upper_candidates(db, user_id, affected)
    db.commit()
    return {
        "accepted": accepted,
        "duplicates": duplicates,
        "rejected": rejected,
        "upper_items": upper_items,
    }


def sync_client_events_safe(db: Session, user_id: str, device_id: str, events: list[dict]) -> dict:
    """sync_client_events 并发安全包装（同 client_event_id 并发 → 唯一索引冲突 → 重试幂等）"""
    from sqlalchemy.exc import IntegrityError

    try:
        return sync_client_events(db, user_id, device_id, events)
    except IntegrityError:
        db.rollback()
        return sync_client_events(db, user_id, device_id, events)
