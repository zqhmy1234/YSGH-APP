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
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, Event, EventItem
from app.services.event_aggregation.pipeline import RawPhoto

# 波D ①（2026-09-10）拆分：写库层已搬至 aggregate_write.py；此处 re-export 保持
# `app.services.events.aggregate.<name>` 与 `app.services.events.<name>` 两条既有
# 导入路径全部可用（消费方零改动）。
from app.services.events.aggregate_write import (  # noqa: F401
    _l3_confidence,
    _previous_aggregate_result,
    _write_l1_days,
    _write_upper_candidates,
    _write_upper_events,
)

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
            Content.source != "seed",  # 2026-09-06 峰宝拍板：seed 注入数据不进聚类（回声卡演示仍可见）
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
            Content.source != "seed",  # seed 不参与 L2/L3 候选（端侧提交路径同样排除）
        )
    ).scalars().all()
    if not rows:
        return 0
    l2, l3 = _l2l3_candidates_from_photos([RawPhoto(**_to_raw_photo(c)) for c in rows])
    return _write_upper_candidates(db, user_id, l2, l3)


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
