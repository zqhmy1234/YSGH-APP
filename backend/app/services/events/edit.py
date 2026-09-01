"""用户手动操作（F5/R1#5 拆包：services/events.py → services/events/edit.py）

职责：merge / split / confirm / set_cover 等用户手动操作（B3-5）。
用户操作优先：手动合并/拆分/确认后，自动算法永不覆盖用户决定（AGG-013）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventEditLog, EventItem
from app.services.errors import ConflictError, NotFoundError, ValidationError

logger = logging.getLogger("yishu.events")


def _get_event(db: Session, user_id: str, event_id: str) -> Event:
    """取事件并校验归属（不存在/非本人 → 抛 NotFoundError，api 层映射 404）"""
    ev = db.execute(
        select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
    ).scalar_one_or_none()
    if ev is None or str(ev.user_id) != user_id:
        raise NotFoundError(f"事件不存在或不属于当前用户: {event_id}")
    return ev


def get_event_items(db: Session, user_id: str, event_id: str) -> list[dict]:
    """事件成员明细（2026-08-25 · S-MO split UI 前置）

    按 taken_at 升序返回成员（照片/文字/语音统一为 title 展示字段），
    供客户端勾选拆分成新事件。归属校验同 _get_event（他人事件 404）。
    """
    _get_event(db, user_id, event_id)
    rows = db.execute(
        select(Content)
        .join(EventItem, EventItem.content_id == Content.id)
        .where(EventItem.event_id == event_id)
        .order_by(Content.taken_at.asc().nulls_last(), Content.created_at.asc())
    ).scalars().all()
    return [
        {
            "content_id": str(c.id),
            "content_type": c.content_type,
            "title": (c.text or "")[:80] if c.text else None,
            "taken_at": c.taken_at,
            "place": c.place,
        }
        for c in rows
    ]


def _log_edit(db: Session, user_id: str, event_id: str, action: str, detail: dict | None = None) -> None:
    """记录用户手动操作痕迹（B3-5；审计/回滚依据）"""
    db.add(EventEditLog(user_id=user_id, event_id=event_id, action=action, detail=detail))


def merge_events(db: Session, user_id: str, target_id: str, source_ids: list[str]) -> Event:
    """用户手动合并（B3-5/AGG-013）：source 事件内容并入 target，source 软删。

    用户操作优先：算法（自动聚合）永不覆盖用户决定——合并后 target 置为
    confirmed，自动聚合不再拆分/改动它。
    """
    target = _get_event(db, user_id, target_id)
    moved = 0
    # 目标已有内容集合（一次查询，消除逐条 dup 检查 N+1，审查 P1-11）
    target_cids = set(
        db.execute(
            select(EventItem.content_id).where(EventItem.event_id == target.id)
        ).scalars().all()
    )
    for sid in source_ids:
        src = _get_event(db, user_id, sid)
        if src.id == target.id:
            continue
        # 转移成员（一次拉全）
        items = db.execute(
            select(EventItem).where(EventItem.event_id == src.id)
        ).scalars().all()
        for it in items:
            if it.content_id not in target_cids:
                db.add(EventItem(content_id=it.content_id, event_id=target.id))
                target_cids.add(it.content_id)
                moved += 1
        # 源事件软删（保留痕迹，可审计）
        src.deleted_at = datetime.now(timezone.utc)
        src.deleted_by = user_id
    # 更新时间窗覆盖合并范围
    if moved:
        # 2026-08-25 修复：autoflush=False，新增 EventItem 未落库时 _refresh_event_window
        # 查不到新成员 → 窗口/标题条数漏算（真机拆分子验证暴露同型 bug）。先 flush。
        db.flush()
        _refresh_event_window(db, target)
    target.status = "confirmed"  # 用户背书：不再被算法改动
    _log_edit(db, user_id, str(target.id), "merge", {"sources": source_ids, "moved": moved})
    db.commit()
    return target


def split_event(db: Session, user_id: str, event_id: str, content_ids: list[str]) -> Event:
    """用户手动拆分（B3-5）：从事件中拆出指定内容 → 新建独立事件。"""
    ev = _get_event(db, user_id, event_id)
    if not content_ids:
        raise ValidationError("拆分内容列表不能为空")
    # 校验内容确实属于该事件
    owned = set(
        db.execute(
            select(EventItem.content_id).where(EventItem.event_id == ev.id)
        ).scalars().all()
    )
    invalid = [c for c in content_ids if c not in owned]
    if invalid:
        raise ConflictError(f"内容不属于该事件: {invalid}")
    # 新建独立事件（level 同源；时间窗取拆出内容）
    new_ev = Event(
        user_id=user_id,
        level=ev.level,
        title=ev.title,
        title_source="user",
        confidence=0.9,
        status="confirmed",  # 用户操作结果不被算法改动
        generated_by="user",
    )
    db.add(new_ev)
    db.flush()
    for cid in content_ids:
        # 从原事件移除 → 挂到新事件
        db.execute(
            EventItem.__table__.delete().where(
                EventItem.event_id == ev.id, EventItem.content_id == cid
            )
        )
        db.add(EventItem(content_id=cid, event_id=new_ev.id))
    # 2026-08-25 修复：autoflush=False，先落库成员变更，_refresh_event_window 才能
    # 读到新成员（此前新事件 start_time=None → 时间轴分组到 1970/1月1日）
    db.flush()
    _refresh_event_window(db, ev)
    _refresh_event_window(db, new_ev)
    _log_edit(db, user_id, str(new_ev.id), "split", {"source_event": event_id, "contents": content_ids})
    db.commit()
    return new_ev


def confirm_event(db: Session, user_id: str, event_id: str, title: str | None = None) -> Event:
    """用户确认（置信度<0.7 转正；用户背书后算法不再改动，AGG-013）"""
    ev = _get_event(db, user_id, event_id)
    ev.status = "confirmed"
    if title:
        ev.title = title
        ev.title_source = "user"
    ev.confidence = 1.0
    _log_edit(db, user_id, str(ev.id), "confirm", {"title": title})
    db.commit()
    return ev


def set_event_cover(db: Session, user_id: str, event_id: str, cover_content_id: str | None) -> Event:
    """用户手动换封面（B3-4：封面可编辑；cover_content_id 必须是事件成员）"""
    ev = _get_event(db, user_id, event_id)
    if cover_content_id:
        owned = {
            str(x) for x in db.execute(
                select(EventItem.content_id).where(EventItem.event_id == ev.id)
            ).scalars().all()
        }
        if str(cover_content_id) not in owned:
            raise ConflictError("封面内容不属于该事件")
        ev.cover_content_id = cover_content_id
    else:
        ev.cover_content_id = None
    _log_edit(db, user_id, str(ev.id), "set_cover", {"cover_content_id": cover_content_id})
    db.commit()
    return ev


def _refresh_event_window(db: Session, ev: Event) -> None:
    """按成员内容重算事件时间窗（合并/拆分后）"""
    from app.db.models import Content

    cids = [
        r for r in db.execute(
            select(EventItem.content_id).where(EventItem.event_id == ev.id)
        ).scalars().all()
    ]
    if not cids:
        ev.start_time = None
        ev.end_time = None
        return
    rows = db.execute(select(Content.taken_at).where(Content.id.in_(cids))).scalars().all()
    ts = [r for r in rows if r is not None]
    if ts:
        ev.start_time = min(ts)
        ev.end_time = max(ts)
    # 标题条数更新（若为模板生成）
    if ev.title_source == "template":
        day_key = (ev.start_time or ev.created_at).astimezone().strftime("%Y-%m-%d")
        ev.title = f"{day_key} · {len(cids)}条"
