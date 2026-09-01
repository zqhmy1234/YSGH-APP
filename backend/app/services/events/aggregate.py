"""事件聚合（F5/R1#5 拆包：services/events.py → services/events/aggregate.py）

职责：从 contents 表读用户内容 → event_aggregation.aggregate()
（L0 聚类 + L1 日聚合 + L2/L3 候选）→ 写 events + event_items 表。

本模块是事件聚合域的**窄端口**：对外只暴露 aggregate_user（同步服务）与
run_user_aggregation（F3 独立 per-user RQ 任务）；event_aggregation 底层聚类
细节（RawPhoto / incremental_aggregate / st_dbscan 等）不外泄到 pipeline。

设计（B3-6 分置）：
- L1 日卡片为主落库（产品验收口径：日常是日子不是事）；L0/L2/L3 候选暂不落库
- 增量聚合：跳过已有关联的 content（幂等）
- 失败静默：聚合失败不影响内容状态（用户无感知，记录 extra.error）
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventItem
from app.services.event_aggregation.pipeline import RawPhoto

logger = logging.getLogger("yishu.events")

_AGG_BATCH = 200

# 增量游标窗口（S6-1 性能债）：L2/L3 只依赖近邻时间窗口——
# L3 是 7 天滑动窗、L2 是 L0 时间簇（≈1h）跨天归并。超过该窗口仍未成候选的
# 内容不会与未来内容形成候选（窗口已过期），不再逐次全量重扫（O(N²)→近线性）。
_AGG_WINDOW_DAYS = 30


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
    if mode != "full":
        # S6-1 增量游标化：l2l3 只扫增量窗口内的未成候选内容，不再全量重扫远古内容
        stmt = stmt.where(
            Content.created_at >= datetime.now(timezone.utc) - timedelta(days=_AGG_WINDOW_DAYS)
        )
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
    #    S6-1 增量接线：以"已落库 level>=2 候选"重建 previous 状态，新内容增量并入
    #    （incremental_aggregate 先匹配后分裂）；失败回退到本批独立候选（不丢候选）。
    from app.services.event_aggregation.pipeline import incremental_aggregate

    try:
        prev = _previous_aggregate_result(db, user_id)
        if prev is not None:
            merged = incremental_aggregate(prev, photos)
            l2, l3 = merged.l2_candidates, merged.l3_candidates
        else:
            l2, l3 = _l2l3_candidates_from_photos(photos)
    except Exception as exc:  # noqa: BLE001 —— 增量失败回退本批候选（用户无感知）
        logger.warning("增量聚合失败 user=%s，回退本批候选: %s", user_id, exc)
        try:
            l2, l3 = _l2l3_candidates_from_photos(photos)
        except Exception as exc2:  # noqa: BLE001 —— 失败静默
            logger.warning("L2/L3 候选失败 user=%s: %s", user_id, exc2)
            return {"l0": 0, "l1": 0, "items": 0, "upper_items": 0, "skipped": 0, "error": str(exc2)}
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

    S6-1 性能修复：批量预载本用户 level>=2 事件+成员+确认态一次（N+1 上提循环外）；
    已存在候选（成员组合相同 → 同一候选已落库）跳过 LLM 裁决与重查——
    批量导入从 O(N²)（每个候选反复 LLM + 逐候选重查）降到近线性。
    """
    from app.services.llm_ops.event_merge import merge_verdict

    # ── 批量预载（S6-2：每用户各 1 次查询）──
    ev_rows = db.execute(
        select(Event).where(
            Event.user_id == user_id,
            Event.level >= 2,
            Event.deleted_at.is_(None),
        )
    ).scalars().all()
    members_by_event: dict[str, set[str]] = defaultdict(set)
    linked_by_member: dict[str, set[str]] = defaultdict(set)
    if ev_rows:
        for eid, cid in db.execute(
            select(EventItem.event_id, EventItem.content_id).where(
                EventItem.event_id.in_([e.id for e in ev_rows])
            )
        ).all():
            members_by_event[str(eid)].add(str(cid))
            linked_by_member[str(cid)].add(str(eid))

    # B3-5 confirmed 保护预载：用户背书事件标题（含改名）+ 成员命中集合
    confirmed_titles: list[str] = []
    confirmed_member_events: list[set[str]] = []
    for e in ev_rows:
        if e.status == "confirmed":
            if e.title_source == "user":
                confirmed_titles.append(e.title or "")
            confirmed_member_events.append(members_by_event.get(str(e.id), set()))
    l3_titles = {e.title for e in ev_rows if e.level == 3}

    added = 0

    # ── L2：已存在候选跳过 LLM 裁决与重查（S6-1 核心）──
    for cand in l2_candidates:
        members = [str(m) for m in (cand.get("cluster") or [])]
        if not members:
            continue
        mset = set(members)
        # 同成员组合候选已落库（已被某 level>=2 事件完整覆盖）→ 跳过 LLM/重查
        if any(mset <= covered for covered in members_by_event.values()):
            continue
        linked = {m for m in members if m in linked_by_member}
        todo = [m for m in members if m not in linked]
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
        # 本批内后续候选也可见该新事件（成员组合去重）
        members_by_event[str(ev.id)] = set(todo)
        for mid in todo:
            linked_by_member[mid].add(str(ev.id))

    # ── L3 ──
    # 归属校验批量预载（S6-2：全部 L3 候选成员一次 IN 查询）
    owned_set: set[str] = set()
    all_l3_members = {str(m) for c in l3_candidates for m in (c.get("cluster") or [])}
    if all_l3_members:
        owned_set = {
            str(r) for r in db.execute(
                select(Content.id).where(
                    Content.id.in_(all_l3_members), Content.user_id == user_id
                )
            ).scalars().all()
        }

    for cand in l3_candidates:
        tag = cand.get("tag")
        if not tag:
            continue
        # B3-5 confirmed 保护（预载数据，无逐候选查询）：
        # ① 用户已确认/改名事件标题含该标签 → 不重建
        if any(tag in (t or "") for t in confirmed_titles):
            continue
        cluster = [str(m) for m in (cand.get("cluster") or [])]
        # ② 候选窗口成员已挂用户背书事件（level>=2 confirmed）→ 不重建
        if cluster and any(
            any(str(m) in ce for ce in confirmed_member_events) for m in cluster
        ):
            continue
        # 同标签 L3 流已落库 → 不重建
        if f"标签 · {tag}" in l3_titles:
            continue
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
        linked_l3 = {m for m in cluster if m in linked_by_member}
        for mid in cluster:
            if str(mid) not in owned_set or str(mid) in linked_l3:
                continue
            db.add(EventItem(content_id=mid, event_id=ev.id))
            added += 1
        # 本批新事件纳入映射（供批内后续候选去重）
        members_by_event[str(ev.id)] = set(cluster)
        for mid in cluster:
            linked_by_member[mid].add(str(ev.id))
    return added


def _previous_aggregate_result(db: Session, user_id: str):
    """已落库 level>=2 候选 → AggregateResult（incremental_aggregate 增量基线）

    S6-1：以"已落库候选成员"重建 previous.l0_clusters（每个候选事件=一组照片），
    新内容经 incremental_aggregate 先匹配并入已有候选（跨天/跨标签候选不丢）。
    l1_days 置空（端侧真值，云侧 l2l3 不重建）。无候选 → None（走本批独立候选）。
    """
    from app.services.event_aggregation.pipeline import AggregateResult
    from app.services.event_aggregation.st_dbscan import Photo

    events = db.execute(
        select(Event).where(
            Event.user_id == user_id,
            Event.level >= 2,
            Event.deleted_at.is_(None),
        )
    ).scalars().all()
    if not events:
        return None
    ev_ids = [e.id for e in events]
    rows = db.execute(
        select(EventItem.event_id, EventItem.content_id).where(
            EventItem.event_id.in_(ev_ids)
        )
    ).all()
    member_ids = [str(cid) for _, cid in rows]
    if not member_ids:
        return None
    content_map = {
        str(c.id): c for c in db.execute(
            select(Content).where(Content.id.in_(member_ids))
        ).scalars().all()
    }
    groups: dict[str, list[Photo]] = defaultdict(list)
    for eid, cid in rows:
        c = content_map.get(str(cid))
        if c is None:
            continue
        extra = c.extra or {}
        groups[str(eid)].append(
            Photo(
                id=str(c.id),
                ts=c.taken_at or c.created_at,
                lat=c.gps_lat,
                lng=c.gps_lng,
                tags=[c.content_class] if c.content_class else (extra.get("ci_tags") or []),
                ocr_text=extra.get("ocr_text") or c.text,
                quality=extra.get("quality_score"),
                face_count=extra.get("face_count"),
            )
        )
    return AggregateResult(
        l0_clusters=[sorted(g, key=lambda p: p.ts) for g in groups.values()],
        l1_days=[],
        l2_candidates=[],
        l3_candidates=[],
        stats={},
    )


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


def _write_upper_events(db: Session, user_id: str, result) -> int:
    """L2/L3 候选落库（全量管线路径兼容封装，委托 _write_upper_candidates）

    注意（S-SY-2）：委托实现按 level>=2 幂等检查（照片已挂 L1 不拦截 L2/L3）。
    """
    return _write_upper_candidates(db, user_id, result.l2_candidates, result.l3_candidates)


def run_user_aggregation(user_id: str, mode: str = "l2l3") -> dict:
    """RQ 任务：云侧事件聚合（F3/R5-3 独立 per-user 任务）

    - 独立 Session 执行（worker 进程），不入队时所在请求事务
    - 失败静默：聚合失败不影响内容状态（用户无感知，记日志）
    - 幂等：aggregate_user 跳过已有关联内容；RQ 重投安全

    调用方（process_content 尾段 / 手动触发）经 core/queue.enqueue_unique
    按 user 级 key 去重合并——同用户同时多内容只跑一次聚合。
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        return aggregate_user(db, str(user_id), mode=mode)
    except Exception as exc:  # noqa: BLE001 —— 聚合失败静默（用户无感知）
        logger.warning("聚合任务失败 user=%s: %s", user_id, exc)
        return {"error": str(exc)}
    finally:
        db.close()
