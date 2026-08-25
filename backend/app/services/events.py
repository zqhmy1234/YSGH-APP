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

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventEditLog, EventItem, OfflineQueue
from app.services.event_aggregation.pipeline import RawPhoto

logger = logging.getLogger("yishu.events")

_AGG_BATCH = 200


def _to_raw_photo(c: Content) -> dict:
    """Content → pipeline RawPhoto 兼容 dict

    B3 #6 OCR 内容维：优先取 extra.ocr_text（腾讯 CI OCR），回退 caption text。
    B3-4 封面：extra.quality_score / extra.face_count（腾讯 CI 人脸标签，缺省 None）。
    """
    extra = c.extra or {}
    return {
        "id": str(c.id),
        "ts": c.taken_at or c.created_at,
        "lat": c.gps_lat,
        "lng": c.gps_lng,
        "tags": [c.content_class] if c.content_class else (extra.get("ci_tags") or []),
        "ocr_text": extra.get("ocr_text") or c.text or None,
        "quality": extra.get("quality_score"),
        "face_count": extra.get("face_count"),
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
            # B3-5 confirmed 保护：用户已背书（confirmed + title_source=user）的 L1
            # 不再被算法追加成员/更新时间窗（用户操作优先，自动算法永不覆盖用户决定）
            if existing.status == "confirmed" and existing.title_source == "user":
                continue
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
    """L2/L3 候选落库（B3-6；幂等：成员已关联 level>=2 则跳过）

    Wave2-AgentD：
      - L2 经 llm_ops/event_merge.merge_verdict 裁决（只看元数据）：
        confidence ≥0.7 转正（status=confirmed + title_source=llm），<0.7 保持 draft 进待确认
      - L2/L3 封面 cover_content_id 赋值（B3-4）
      - L3 主题流携带 7 天窗成员（cluster）→ 真实挂 event_items（生命周期/封面可派生）
      - B3-5 confirmed 保护：用户已确认/改名的同标签 L3 不重建；成员已挂用户背书事件不重建

    修复（S-SY-2）：原 _write_upper_events 按"已关联任意事件"跳过——
    端侧 L0/L1 真值后照片普遍已挂 L1 日卡片，会误跳 L2/L3；
    改为只查 level>=2 事件（L1 关联不再拦截候选生成）。
    """
    from app.services.llm_ops.event_merge import merge_verdict

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
        tag = cand.get("tag") or (cand.get("tag_hint") or [None])[0] or "未命名主题"
        # LLM 归并裁决（B3-2：只看元数据；mock 通道确定性兜底）
        verdict = merge_verdict(cand)
        confidence = max(0.0, min(1.0, verdict.get("confidence", 0.6)))
        status = "confirmed" if confidence >= 0.7 else "draft"   # ≥0.7 转正 / <0.7 待确认
        title_source = "llm" if verdict.get("llm") == "real" else "template"
        title = verdict.get("title") or f"主题 · {tag}（{len(todo)} 条）"
        ev = Event(
            user_id=user_id,
            level=2,
            title=title,
            title_source=title_source,
            start_time=start_ts,
            end_time=end_ts,
            place=cand.get("place_hint"),
            cover_content_id=cand.get("cover_content_id"),   # B3-4 封面（人脸+质量分+时间居中）
            confidence=confidence,
            status=status,
            generated_by="cloud-llm" if verdict.get("llm") == "real" else "cloud-proto",
        )
        db.add(ev)
        db.flush()
        for mid in todo:
            db.add(EventItem(content_id=mid, event_id=ev.id))
            added += 1

    for cand in l3_candidates:
        tag = cand.get("tag")
        if not tag:
            continue
        # B3-5 confirmed 保护：用户已确认/改名的同标签主题流不重建（含成员重叠）
        if _l3_confirmed_exists(db, user_id, tag, cand.get("cluster") or []):
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
        cluster = cand.get("cluster") or []
        tr = cand.get("time_range") or []
        try:
            start_ts = datetime.fromisoformat(tr[0]) if tr and tr[0] else None
            end_ts = datetime.fromisoformat(tr[1]) if len(tr) > 1 and tr[1] else None
        except ValueError:
            start_ts = end_ts = None
        ev = Event(
            user_id=user_id,
            level=3,
            title=f"标签 · {tag}",
            title_source="template",
            start_time=start_ts,
            end_time=end_ts,
            cover_content_id=cand.get("cover_content_id"),   # B3-4 L3 独立封面（不居中）
            confidence=_l3_confidence(cand),
            status="draft",
            generated_by="cloud-proto",
        )
        db.add(ev)
        db.flush()
        # L3 主题流真实挂成员（B3：照片↔事件多对多；生命周期/封面据此派生）
        linked_l3 = set(
            db.execute(
                select(EventItem.content_id)
                .join(Event, Event.id == EventItem.event_id)
                .where(
                    EventItem.content_id.in_(cluster),
                    Event.user_id == user_id,
                    Event.level >= 2,
                    Event.deleted_at.is_(None),
                )
            ).scalars().all()
        )
        # 归属校验：只挂当前用户的内容（候选源自用户照片，防御性校验）
        owned_l3 = set(
            str(r) for r in db.execute(
                select(Content.id).where(Content.id.in_(cluster), Content.user_id == user_id)
            ).scalars().all()
        )
        for mid in cluster:
            if str(mid) not in owned_l3 or str(mid) in linked_l3:
                continue
            db.add(EventItem(content_id=mid, event_id=ev.id))
            added += 1
    return added


def _l3_confirmed_exists(db: Session, user_id: str, tag: str, cluster: list[str]) -> bool:
    """B3-5 confirmed 保护：用户已背书事件与候选主题流冲突则跳过重建

    ① 存在用户已确认（confirmed + title_source=user）的 L2/L3 事件标题含该标签
       （用户改名后标题变化 → 防算法按"标签 · {tag}"重建同名流）
    ② 候选窗口成员已挂到用户已确认的 level>=2 事件
    """
    titles = db.execute(
        select(Event.title).where(
            Event.user_id == user_id,
            Event.deleted_at.is_(None),
            Event.level >= 2,
            Event.status == "confirmed",
            Event.title_source == "user",
        )
    ).scalars().all()
    if any(tag in (t or "") for t in titles):
        return True
    if cluster:
        linked = db.execute(
            select(Event.id)
            .join(EventItem, EventItem.event_id == Event.id)
            .where(
                EventItem.content_id.in_(cluster),
                Event.user_id == user_id,
                Event.level >= 2,
                Event.status == "confirmed",
                Event.deleted_at.is_(None),
            )
        ).scalar_one_or_none()
        if linked is not None:
            return True
    return False


def _l3_confidence(cand: dict) -> float:
    """L3 置信度（B3-5：标签强度——7 天窗内次数；弱流已被 7 天窗 ≥3 次过滤）"""
    count = cand.get("count", 0)
    return min(0.95, 0.5 + count * 0.05)


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
        by_day.setdefault(day_key, []).append(
            _P(
                id=p.id, ts=ts, lat=p.lat, lng=p.lng, tags=p.tags or [],
                ocr_text=p.ocr_text, quality=p.quality, face_count=p.face_count,
            )
        )
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


def _get_event(db: Session, user_id: str, event_id: str) -> Event:
    """取事件并校验归属（不存在/非本人 → 抛 ValueError）"""
    ev = db.execute(
        select(Event).where(Event.id == event_id, Event.deleted_at.is_(None))
    ).scalar_one_or_none()
    if ev is None or str(ev.user_id) != user_id:
        raise ValueError(f"事件不存在或不属于当前用户: {event_id}")
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
            raise ValueError("封面内容不属于该事件")
        ev.cover_content_id = cover_content_id
    else:
        ev.cover_content_id = None
    _log_edit(db, user_id, str(ev.id), "set_cover", {"cover_content_id": cover_content_id})
    db.commit()
    return ev


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
