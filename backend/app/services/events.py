"""事件聚合服务（B3 · 2026-08-20 AI 管线接线）

职责：从 contents 表读用户内容 → event_aggregation.aggregate()
（L0 聚类 + L1 日聚合 + L2/L3 候选）→ 写 events + event_items 表。

设计（B3-6 分置）：
- L1 日卡片为主落库（产品验收口径：日常是日子不是事）；L0/L2/L3 候选暂不落库
- 增量聚合：跳过已有关联的 content（幂等）
- 失败静默：聚合失败不影响内容状态（用户无感知，记录 extra.error）
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventEditLog, EventItem

logger = logging.getLogger("yishu.events")

_AGG_BATCH = 200


def _to_raw_photo(c: Content) -> dict:
    """Content → pipeline RawPhoto 兼容 dict"""
    return {
        "id": str(c.id),
        "ts": c.taken_at or c.created_at,
        "lat": c.gps_lat,
        "lng": c.gps_lng,
        "tags": [c.content_class] if c.content_class else [],
        "ocr_text": c.text or None,
        "source": c.source or "app",
    }


def aggregate_user(db: Session, user_id: str, since: datetime | None = None) -> dict:
    """对用户未聚合内容跑完整管线，写 L1 日卡片 events + event_items。

    since：增量游标（None = 首次全量）。返回统计 dict。
    （P2-03 重构：管线已移入 backend 包 app.services.event_aggregation，
    删除原运行时 sys.path hack + 对仓库根 research/ 的反向依赖）
    """
    from app.services.event_aggregation.pipeline import RawPhoto, aggregate

    # 1. 查候选内容（未删除即可——不限于 done：process_content 回写 done 前
    #    先聚合，自身 status=processing 也要能入事件，否则增量触发错位（2026-08-20 实测））
    stmt = (
        select(Content)
        .where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
        )
        .order_by(Content.created_at)
        .limit(_AGG_BATCH)
    )
    if since is not None:
        stmt = stmt.where(Content.created_at > since)
    contents = db.execute(stmt).scalars().all()
    if not contents:
        return {"l0": 0, "l1": 0, "items": 0, "skipped": 0}

    # 2. 跳过已聚合
    cids = [c.id for c in contents]
    linked = set(
        db.execute(
            select(EventItem.content_id).where(EventItem.content_id.in_(cids))
        ).scalars().all()
    )
    todo = [c for c in contents if str(c.id) not in linked]
    skipped = len(contents) - len(todo)
    if not todo:
        return {"l0": 0, "l1": 0, "items": 0, "skipped": skipped}

    # 3. 跑完整管线
    try:
        result = aggregate([RawPhoto(**_to_raw_photo(c)) for c in todo])
    except Exception as exc:  # noqa: BLE001 —— 聚合失败静默（用户无感知）
        logger.warning("聚合失败 user=%s: %s", user_id, exc)
        return {"l0": 0, "l1": 0, "items": 0, "skipped": skipped, "error": str(exc)}

    # 4. 写 L1 日卡片（产品口径：稀疏并入日卡片，B3 #8）
    #    同日去重：当天已有 L1 事件则并入（追加 event_items + 更新时间窗），
    #    不重复建（2026-08-20 E2E 实测：增量触发导致同日拆成多个事件）
    items = 0
    for day in result.l1_days:
        members = [p.id for p in day.get("photos", [])]
        if not members:
            continue
        day_key = day.get("date", "")
        # 从成员照片推导时间窗（pipeline day dict 无 start/end 字段，2026-08-20 实测）
        _ts = [p.ts for p in day.get("photos", []) if getattr(p, "ts", None)]
        start_ts = min(_ts) if _ts else None
        end_ts = max(_ts) if _ts else None
        existing = None
        if day_key:
            try:
                day_start = datetime.strptime(day_key, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            except ValueError:
                day_start = None
            if day_start is not None:
                existing = db.execute(
                    select(Event).where(
                        Event.user_id == user_id,
                        Event.level == 1,
                        Event.deleted_at.is_(None),
                        Event.start_time >= day_start,
                        Event.start_time < day_start + timedelta(days=1),
                    ).order_by(Event.created_at.desc())
                ).scalars().first()
        if existing is not None:
            # 并入：更新时间窗 + 追加成员
            if start_ts and (existing.start_time is None or start_ts < existing.start_time):
                existing.start_time = start_ts
            if end_ts and (existing.end_time is None or end_ts > existing.end_time):
                existing.end_time = end_ts
            for mid in members:
                db.add(EventItem(content_id=mid, event_id=existing.id))
                items += 1
            continue
        ev = Event(
            user_id=user_id,
            level=1,
            title=f"{day_key} · {len(members)}条",
            title_source="template",
            start_time=start_ts,
            end_time=end_ts,
            place=None,
            confidence=0.9,
            status="draft",
            generated_by="cloud",
        )
        db.add(ev)
        db.flush()
        for mid in members:
            db.add(EventItem(content_id=mid, event_id=ev.id))
            items += 1

    # 5. L2/L3 候选落库（P2-07：此前只落 L1，L2/L3 明标"原型占位"无数据）
    #    候选级 draft 事件（标题模板；LLM 语义归并待真实数据到位后替换为云侧裁决）
    upper_items = _write_upper_events(db, user_id, result)
    db.commit()
    return {
        "l0": len(result.l0_clusters), "l1": len(result.l1_days),
        "items": items, "skipped": skipped, "upper_items": upper_items,
    }


def _write_upper_events(db: Session, user_id: str, result) -> int:
    """L2/L3 候选落库为 draft 事件（P2-07；幂等：同成员已关联则跳过）

    L2：跨天 ≥2 天 ≥10 张的标签归并候选 → level=2 事件
    L3：同标签 7 天 ≥3 次 → level=3 主题流事件
    返回新增 upper 事件成员数。
    """
    from datetime import datetime as _dt

    added = 0
    for cand in result.l2_candidates:
        members = cand.get("cluster") or []
        if not members:
            continue
        linked = set(
            db.execute(
                select(EventItem.content_id).where(EventItem.content_id.in_(members))
            ).scalars().all()
        )
        todo = [m for m in members if str(m) not in linked]
        if not todo:
            continue
        tr = cand.get("time_range") or []
        try:
            start_ts = _dt.fromisoformat(tr[0]) if tr and tr[0] else None
            end_ts = _dt.fromisoformat(tr[1]) if len(tr) > 1 and tr[1] else None
        except ValueError:
            start_ts = end_ts = None
        tag = cand.get("tag") or cand.get("tag_hint") or "未命名主题"
        ev = Event(
            user_id=user_id,
            level=2,
            title=f"主题 · {tag}（{len(todo)} 条）",
            title_source="template",
            start_time=start_ts,
            end_time=end_ts,
            place=cand.get("place_hint"),
            confidence=0.6,  # 候选级置信度（LLM 裁决后提升）
            status="draft",
            generated_by="cloud-proto",  # 候选落库（非最终 LLM 归并）
        )
        db.add(ev)
        db.flush()
        for mid in todo:
            db.add(EventItem(content_id=mid, event_id=ev.id))
            added += 1

    for cand in result.l3_candidates:
        tag = cand.get("tag")
        count = cand.get("count", 0)
        if not tag:
            continue
        # L3 主题流：不绑定具体内容（标签级事件，跨天多次出现）
        exists = db.execute(
            select(Event.id).where(
                Event.user_id == user_id,
                Event.level == 3,
                Event.deleted_at.is_(None),
                Event.title == f"标签 · {tag}",
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        db.add(
            Event(
                user_id=user_id,
                level=3,
                title=f"标签 · {tag}",
                title_source="template",
                confidence=0.6,
                status="draft",
                generated_by="cloud-proto",
            )
        )
        added += count
    return added


def get_timeline(db: Session, user_id: str, level: int | None = None) -> list[Event]:
    """时间轴（F8）：用户事件列表，按 start_time 倒序"""
    stmt = (
        select(Event)
        .where(Event.user_id == user_id, Event.deleted_at.is_(None))
        .order_by(Event.start_time.desc())
    )
    if level is not None:
        stmt = stmt.where(Event.level == level)
    return db.execute(stmt).scalars().all()


def _get_event(db: Session, user_id: str, event_id: str) -> Event:
    """取事件并校验归属（不存在/非本人 → 抛 ValueError）"""
    ev = db.execute(
        select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
    ).scalar_one_or_none()
    if ev is None or str(ev.user_id) != user_id:
        raise ValueError(f"事件不存在或不属于当前用户: {event_id}")
    return ev


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
        _refresh_event_window(db, target)
    target.status = "confirmed"  # 用户背书：不再被算法改动
    _log_edit(db, user_id, str(target.id), "merge", {"sources": source_ids, "moved": moved})
    db.commit()
    return target


def split_event(db: Session, user_id: str, event_id: str, content_ids: list[str]) -> Event:
    """用户手动拆分（B3-5）：从事件中拆出指定内容 → 新建独立事件。"""
    ev = _get_event(db, user_id, event_id)
    if not content_ids:
        raise ValueError("拆分内容列表不能为空")
    # 校验内容确实属于该事件
    owned = set(
        db.execute(
            select(EventItem.content_id).where(EventItem.event_id == ev.id)
        ).scalars().all()
    )
    invalid = [c for c in content_ids if c not in owned]
    if invalid:
        raise ValueError(f"内容不属于该事件: {invalid}")
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
