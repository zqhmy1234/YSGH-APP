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

from app.db.models import Content, Event, EventEditLog, EventItem, OfflineQueue
from app.services.event_aggregation.pipeline import RawPhoto

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


def aggregate_user(
    db: Session,
    user_id: str,
    since: datetime | None = None,
    mode: str = "l2l3",
) -> dict:
    """云侧事件聚合（B3-6 分置重构 · S-SY-2）

    模式：
      mode="l2l3"（默认）：端侧 L0/L1 真值后，云侧只跑 L2/L3 候选
        （caption/CI 打标仍在 _process_photo；L1 日卡片由端侧 POST /events/sync 提交）
      mode="full"：第一波全量管线（L0+L1+L2/L3），仅用于基线迁移/遗留路径

    since：增量游标（None = 首次全量）。返回统计 dict。
    """
    # 1. 查候选照片（未删除；跳过已产生 level>=2 候选的，幂等）
    linked_l2 = (
        select(EventItem.content_id)
        .join(Event, Event.id == EventItem.event_id)
        .where(
            Event.user_id == user_id,
            Event.level >= 2,
            Event.deleted_at.is_(None),
        )
    )
    stmt = (
        select(Content)
        .where(
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
            Content.id.not_in(linked_l2),
        )
        .order_by(Content.created_at)
        .limit(_AGG_BATCH * 2)
    )
    if since is not None:
        stmt = stmt.where(Content.created_at > since)
    contents = db.execute(stmt).scalars().all()
    if not contents:
        return {"l0": 0, "l1": 0, "items": 0, "upper_items": 0, "skipped": 0}

    photos = [RawPhoto(**_to_raw_photo(c)) for c in contents]

    if mode == "full":
        # 第一波全量管线（基线迁移/遗留）：L0+L1+L2/L3 全跑
        from app.services.event_aggregation.pipeline import aggregate

        # 保留第一波语义：跳过已关联任意事件的内容（防 EventItem 复合主键重复）
        linked_any = select(EventItem.content_id)
        stmt = stmt.where(Content.id.not_in(linked_any))
        contents = db.execute(stmt).scalars().all()
        if not contents:
            return {"l0": 0, "l1": 0, "items": 0, "upper_items": 0, "skipped": 0}
        photos = [RawPhoto(**_to_raw_photo(c)) for c in contents]
        try:
            result = aggregate(photos)
        except Exception as exc:  # noqa: BLE001 —— 聚合失败静默（用户无感知）
            logger.warning("聚合失败 user=%s: %s", user_id, exc)
            return {"l0": 0, "l1": 0, "items": 0, "upper_items": 0, "skipped": 0, "error": str(exc)}

        items = _write_l1_days(db, user_id, result.l1_days)
        upper_items = _write_upper_candidates(db, user_id, result.l2_candidates, result.l3_candidates)
        db.commit()
        return {
            "l0": len(result.l0_clusters), "l1": len(result.l1_days),
            "items": items, "skipped": 0, "upper_items": upper_items,
        }

    # 2. mode="l2l3"：只跑 L2/L3 候选（B3-6；L1 由端侧提交）
    try:
        l2, l3 = _l2l3_candidates_from_photos(photos)
    except Exception as exc:  # noqa: BLE001 —— 失败静默
        logger.warning("L2/L3 候选失败 user=%s: %s", user_id, exc)
        return {"l0": 0, "l1": 0, "items": 0, "upper_items": 0, "skipped": 0, "error": str(exc)}
    upper_items = _write_upper_candidates(db, user_id, l2, l3)
    db.commit()
    return {"l0": 0, "l1": 0, "items": 0, "upper_items": upper_items, "skipped": 0}


def _write_l1_days(db: Session, user_id: str, l1_days: list[dict]) -> int:
    """写 L1 日卡片（全量管线路径；同日去重并入，不重复建）"""
    items = 0
    for day in l1_days:
        members = [p.id for p in day.get("photos", [])]
        if not members:
            continue
        day_key = day.get("date", "")
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
    return items


def _write_upper_candidates(
    db: Session,
    user_id: str,
    l2_candidates: list[dict],
    l3_candidates: list[dict],
) -> int:
    """L2/L3 候选落库为 draft 事件（B3-6；幂等：成员已关联 level>=2 则跳过）

    修复（S-SY-2）：原 _write_upper_events 按"已关联任意事件"跳过——
    端侧 L0/L1 真值后照片普遍已挂 L1 日卡片，会误跳 L2/L3；
    改为只查 level>=2 事件（L1 关联不再拦截候选生成）。
    """
    added = 0
    for cand in l2_candidates:
        members = cand.get("cluster") or []
        if not members:
            continue
        linked = set(
            db.execute(
                select(EventItem.content_id)
                .join(Event, Event.id == EventItem.event_id)
                .where(
                    EventItem.content_id.in_(members),
                    Event.user_id == user_id,
                    Event.level >= 2,
                    Event.deleted_at.is_(None),
                )
            ).scalars().all()
        )
        todo = [m for m in members if str(m) not in linked]
        if not todo:
            continue
        tr = cand.get("time_range") or []
        try:
            start_ts = datetime.fromisoformat(tr[0]) if tr and tr[0] else None
            end_ts = datetime.fromisoformat(tr[1]) if len(tr) > 1 and tr[1] else None
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
            confidence=0.6,
            status="draft",
            generated_by="cloud-proto",
        )
        db.add(ev)
        db.flush()
        for mid in todo:
            db.add(EventItem(content_id=mid, event_id=ev.id))
            added += 1

    for cand in l3_candidates:
        tag = cand.get("tag")
        count = cand.get("count", 0)
        if not tag:
            continue
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


def _l2l3_candidates_from_photos(photos: list[RawPhoto]) -> tuple[list[dict], list[dict]]:
    """云侧 L2/L3 候选（B3-6：端侧 L0/L1 后云侧只跑 L2/L3）

    L2：按自然日分组建伪簇 → pipeline.l2_candidates（跨天 ≥2 天 ≥10 张标签归并）
    L3：标签 7 天 ≥3 次（pipeline.l3_candidates）
    """
    from app.services.event_aggregation import pipeline as _pl
    from app.services.event_aggregation.st_dbscan import Photo as _P

    by_day: dict[str, list[_P]] = {}
    for p in photos:
        ts = p.ts
        day_key = ts.date().isoformat()
        by_day.setdefault(day_key, []).append(_P(id=p.id, ts=ts, lat=p.lat, lng=p.lng, tags=p.tags or []))
    clusters = [by_day[k] for k in sorted(by_day)]
    return _pl.l2_candidates(clusters), _pl.l3_candidates(photos)


def _refresh_upper_candidates(db: Session, user_id: str, photo_ids: list[str]) -> int:
    """受影响照片的 L2/L3 候选重算（S-SY-1：端侧事件提交后云侧补 L2/L3）"""
    if not photo_ids:
        return 0
    rows = db.execute(
        select(Content).where(
            Content.id.in_(photo_ids),
            Content.user_id == user_id,
            Content.deleted_at.is_(None),
        )
    ).scalars().all()
    if not rows:
        return 0
    l2, l3 = _l2l3_candidates_from_photos([RawPhoto(**_to_raw_photo(c)) for c in rows])
    return _write_upper_candidates(db, user_id, l2, l3)


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

    for item in events:
        cid = item.get("client_event_id")
        exists = db.execute(
            select(Event.id).where(
                Event.user_id == user_id,
                Event.client_event_id == cid,
            )
        ).scalar_one_or_none()
        if exists is not None:
            duplicates.append(cid)
            continue

        photo_ids = item.get("photo_ids") or []
        if not photo_ids:
            rejected.append({"client_event_id": cid, "reason": "photo_ids 为空"})
            continue
        rows = db.execute(
            select(Content.id).where(
                Content.id.in_(photo_ids),
                Content.user_id == user_id,
                Content.deleted_at.is_(None),
            )
        ).scalars().all()
        valid = {str(r) for r in rows}
        invalid = [p for p in photo_ids if p not in valid]
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


def _write_upper_events(db: Session, user_id: str, result) -> int:
    """L2/L3 候选落库（全量管线路径兼容封装，委托 _write_upper_candidates）

    注意（S-SY-2）：委托实现按 level>=2 幂等检查（照片已挂 L1 不拦截 L2/L3）。
    """
    return _write_upper_candidates(db, user_id, result.l2_candidates, result.l3_candidates)


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
