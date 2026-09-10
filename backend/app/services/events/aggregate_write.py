"""事件聚合写入层（波D ① · 2026-09-10 拆分 services/events/aggregate.py）

职责：聚合结果的**落库与增量基线**——L1 日卡片写入、L2/L3 候选写入、
已落库候选 → 增量基线（AggregateResult）重建。

拆分背景（波D ① 巨文件拆分）：本模块由 aggregate.py 按「读/写职责」切出，
**函数体逐字未改**（纯搬迁，零逻辑变更）。aggregate.py 经 re-export 保持
`app.services.events.aggregate.<name>` 与 `app.services.events.<name>` 两条既有
导入路径全部可用——消费方（services/events/__init__.py、tests/test_aggregation.py、
tests/test_event_ops.py）零改动。

依赖方向：本模块不 import aggregate（避免循环）；aggregate 单向 re-export 本模块。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventItem

__all__ = [
    "_l3_confidence",
    "_previous_aggregate_result",
    "_write_l1_days",
    "_write_upper_candidates",
    "_write_upper_events",
]


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
        # 同标签 L3 流已落库 → 不重建；但新成员必须并入已有流（R9-C 修复 2026-09-07）
        # 旧行为 continue = 同标签后续内容永不进事件层（时间轴永远看不到新录音/新内容）——
        # 实锤：4f8fe57e（09-07 21:25 语音）聚合任务正常执行，候选正确并进 mixed 簇，
        # 却在 write 阶段被本分支整体丢弃，EventItem 零挂接
        if f"标签 · {tag}" in l3_titles:
            existing_l3 = next(
                (e for e in ev_rows if e.level == 3 and e.title == f"标签 · {tag}"),
                None,
            )
            # 用户背书的流不动（B3-5：confirmed + 用户手改标题 → 算法永不覆盖）
            if (
                existing_l3 is not None
                and not (existing_l3.status == "confirmed" and existing_l3.title_source == "user")
            ):
                existing_members = members_by_event.get(str(existing_l3.id), set())
                linked_l3 = {m for m in cluster if m in linked_by_member}
                for mid in cluster:
                    if str(mid) not in owned_set or str(mid) in linked_l3:
                        continue
                    if str(mid) in existing_members:
                        continue
                    db.add(EventItem(content_id=mid, event_id=existing_l3.id))
                    added += 1
                # 新成员并入后同步映射（供批内后续候选去重；集合幂等，老成员重复 add 无害）
                for mid in cluster:
                    linked_by_member[mid].add(str(existing_l3.id))
                    existing_members.add(str(mid))
                # 时间窗外延：候选范围比已有流更晚/更早时扩展（start_time 驱动时间轴日分组）
                tr = cand.get("time_range") or []
                try:
                    cand_start = datetime.fromisoformat(tr[0]) if tr and tr[0] else None
                    cand_end = datetime.fromisoformat(tr[1]) if len(tr) > 1 and tr[1] else None
                except ValueError:
                    cand_start = cand_end = None
                if cand_start is not None and (
                    existing_l3.start_time is None or cand_start < existing_l3.start_time
                ):
                    existing_l3.start_time = cand_start
                if cand_end is not None and (
                    existing_l3.end_time is None or cand_end > existing_l3.end_time
                ):
                    existing_l3.end_time = cand_end
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


def _write_upper_events(db: Session, user_id: str, result) -> int:
    """L2/L3 候选落库（全量管线路径兼容封装，委托 _write_upper_candidates）

    注意（S-SY-2）：委托实现按 level>=2 幂等检查（照片已挂 L1 不拦截 L2/L3）。
    """
    return _write_upper_candidates(db, user_id, result.l2_candidates, result.l3_candidates)
