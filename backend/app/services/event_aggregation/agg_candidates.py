"""四层事件聚合 · L2/L3 候选构造与生命周期状态机（B3-2 / B3-4 / B3-6）

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
本模块承载管线的**第三段**——L0/L1 之后向云侧输出的候选结构：

  - L2 候选：跨 L0 簇语义归并候选（标签一致组 + 地点域连续组，携带元数据/封面/OCR）
  - L3 候选：标签主题流（7 天滑动窗口 ≥3 次跨天）+ 生命周期状态机（活跃→静默→归档）
  - 共享叶子工具：时间跨度 / GPS 可信判定 / 地点提示降级 / 标签提示 / OCR 摘要 / 封面选择

本模块不 import pipeline（单向依赖：pipeline → agg_candidates），避免循环导入。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone

from app.services.event_aggregation.agg_types import (
    AGG_CONFIG,
    L2_MAX_GAP_HOURS,
    L2_MAX_GAP_KM,
    L3_ACTIVE_DAYS,
    L3_SILENT_DAYS,
    L3_WINDOW_DAYS,
    RawPhoto,
)
from app.services.event_aggregation.st_dbscan import Photo, haversine_m


def l3_lifecycle(
    start_ts: datetime | None,
    last_activity: datetime | None,
    now: datetime | None = None,
) -> dict:
    """L3 生命周期状态机（B3-2：活跃 30 天 → 静默降级 → 归档）

    纯派生计算（MVP 不落库；如需持久化状态转换，登记表需求由集成 Agent 评估）：
      active    最近活动 ≤30 天（活跃期）
      silent    30-90 天无活动（静默降级：不再主动聚合，可恢复）
      archived  90 天无活动（归档：仅历史可见）
    """
    now = now or datetime.now(timezone.utc)
    if last_activity is None:
        last_activity = start_ts or now
    idle_days = 0
    if last_activity is not None:
        idle_days = max(0, int((now - last_activity).total_seconds() // 86400))
    if idle_days <= L3_ACTIVE_DAYS:
        state = "active"
    elif idle_days <= L3_SILENT_DAYS:
        state = "silent"
    else:
        state = "archived"
    active_days = 0
    if start_ts is not None and last_activity is not None and last_activity >= start_ts:
        active_days = int((last_activity - start_ts).total_seconds() // 86400)
    return {"state": state, "idle_days": idle_days, "active_days": active_days}


def _l2_candidates(clusters: list[list[Photo]]) -> list[dict]:
    """L2 候选：跨 L0 簇的语义归并候选（B3-2）

    触发：跨天（≥2 天）且 ≥10 张，且满足 ①主题标签一致 或 ②地点域连续（5km/12hr）。
    ① 标签一致组：按主导标签分组（并行事件靠标签维度分离）
    ② 地点域连续组：标签组外的照片（多标签长跨度事件）按 5km/12hr 连续归并
    候选携带元数据（时间/地点/标签/OCR 摘要/封面）供云侧 LLM 裁决（只看元数据不全图）。
    """
    tag_groups: dict[str, list] = defaultdict(list)
    for cl in clusters:
        hint = _tag_hint(cl)
        key = hint[0] if hint else "__no_tag__"
        tag_groups[key].append(cl)

    l2_candidates = []
    covered: set[str] = set()
    for tag, cls in tag_groups.items():
        merged = [p for cl in cls for p in cl]
        if (
            tag != "__no_tag__"
            and _span_days(merged) >= AGG_CONFIG["l2_min_days"]
            and len(merged) >= AGG_CONFIG["l2_min_photos"]
        ):
            l2_candidates.append(_make_l2_candidate(merged, tag=tag))
            covered.update(p.id for p in merged)

    # ② 地点域连续：标签组外的照片按 5km/12hr 连续归并
    leftover = [p for cl in clusters for p in cl if p.id not in covered]
    for group in _place_continuous_groups(leftover):
        if _span_days(group) >= AGG_CONFIG["l2_min_days"] and len(group) >= AGG_CONFIG["l2_min_photos"]:
            l2_candidates.append(_make_l2_candidate(group, tag=None))
    return l2_candidates


def _make_l2_candidate(merged: list[Photo], tag: str | None) -> dict:
    """组装 L2 候选（元数据齐备，供 llm_ops/event_merge 裁决）"""
    return {
        "cluster": [p.id for p in merged],
        "tag": tag,
        "time_range": [
            min(p.ts for p in merged).isoformat(),
            max(p.ts for p in merged).isoformat(),
        ],
        "place_hint": _place_hint(merged),
        "tag_hint": _tag_hint(merged),
        "ocr_summary": _ocr_summary(merged),
        "cover_content_id": _pick_cover(merged, level=2),
    }


def _l3_candidates(photos: list[RawPhoto]) -> list[dict]:
    """L3 主题流候选（B3-2）：同一标签 7 天内 ≥3 次（跨天）→ 主题流

    7 天滑动窗口取最大命中数；窗口内照片作为流成员（供封面/生命周期/OCR 摘要）。
    候选含独立封面（人脸+质量分，无时间居中，B3-4 峰宝拍板）。
    """
    by_tag: dict[str, list[RawPhoto]] = defaultdict(list)
    for p in photos:
        for t in (p.tags or []):
            by_tag[t].append(p)

    candidates = []
    for tag, ps in by_tag.items():
        seq = sorted(ps, key=lambda p: p.ts)
        window = _max_in_window(seq, days=L3_WINDOW_DAYS)
        if window is None:
            continue
        window_photos, count, span_days = window
        if count < AGG_CONFIG["l3_tag_threshold"] or span_days < 2:
            continue
        start = min(p.ts for p in window_photos)
        end = max(p.ts for p in window_photos)
        candidates.append(
            {
                "tag": tag,
                "count": count,
                "total_photos": len(seq),
                "time_range": [start.isoformat(), end.isoformat()],
                "cluster": [p.id for p in window_photos],
                "cover_content_id": _pick_cover(window_photos, level=3),
                "ocr_summary": _ocr_summary(window_photos),
            }
        )
    return candidates


def _max_in_window(seq: list, days: int) -> tuple[list, int, int] | None:
    """滑动窗口：返回 (窗口内元素, 数量, 窗口跨天数) 的最大窗口；空序列返回 None"""
    if not seq:
        return None
    best_photos: list = []
    best_count = 0
    best_span = 0
    lo = 0
    for hi in range(len(seq)):
        while (seq[hi].ts - seq[lo].ts).total_seconds() > days * 86400:
            lo += 1
        window = seq[lo:hi + 1]
        span = (window[-1].ts.date() - window[0].ts.date()).days + 1
        if len(window) > best_count or (len(window) == best_count and span > best_span):
            best_photos, best_count, best_span = list(window), len(window), span
    return best_photos, best_count, best_span


def _span_days(cl: list) -> int:
    if not cl:
        return 0
    days = max(p.ts.date() for p in cl) - min(p.ts.date() for p in cl)
    return days.days + 1


def _place_continuous_groups(photos: list[Photo]) -> list[list[Photo]]:
    """按地点域连续（5km/12hr）把照片切分为连续片段（B3-2 地点域触发）"""
    seq = sorted(photos, key=lambda p: p.ts)
    groups: list[list[Photo]] = []
    cur: list[Photo] = []
    for a, b in zip(seq, seq[1:], strict=False):
        cur.append(a)
        dt = (b.ts - a.ts).total_seconds()
        if 0 < dt <= L2_MAX_GAP_HOURS * 3600 and _gps_reliable(a) and _gps_reliable(b):
            if haversine_m(a.lat, a.lng, b.lat, b.lng) > L2_MAX_GAP_KM * 1000:
                groups.append(cur)
                cur = []
    if seq:
        cur.append(seq[-1])
    if cur:
        groups.append(cur)
    return groups


def _gps_reliable(p) -> bool:
    """GPS 可信：有坐标且状态为 ok/corrected（漂移点不参与 L2 地点判断，B3-3 ④）

    getattr 防御：直接喂 RawPhoto（未过 preprocess，无 gps_state）时按可信处理。
    """
    state = getattr(p, "gps_state", "ok")
    return p.lat is not None and p.lng is not None and state in ("ok", "corrected")


def _place_hint(cl: list) -> str | None:
    """地点提示：众数 GPS 反查（原型只返回坐标簇中心）

    B3-3 降级不猜：簇内多数照片坐标存疑（degraded/approx）→ 返回
    "附近/某区"粗粒度提示（约 0.1° ≈ 10km），不猜精确 POI。
    """
    pts = [(p.lat, p.lng) for p in cl if _gps_reliable(p)]
    unreliable = sum(1 for p in cl if getattr(p, "gps_state", "ok") in ("degraded", "approx"))
    if not pts:
        if unreliable:
            return "某区域"
        return None
    lat = sum(p[0] for p in pts) / len(pts)
    lng = sum(p[1] for p in pts) / len(pts)
    if unreliable > len(cl) / 2:
        return f"{lat:.1f},{lng:.1f}附近"   # 粗粒度，不猜 POI
    return f"{lat:.5f},{lng:.5f}"


def _tag_hint(cl: list) -> list[str]:
    tags = [t for p in cl for t in (p.tags or [])]
    return [t for t, _ in Counter(tags).most_common(3)]


def _ocr_summary(photos: list) -> str | None:
    """OCR 摘要：去空去重取前 3 条（各截 60 字）——无 GPS 照片的内容主信号（B3 #6）"""
    seen: list[str] = []
    for p in sorted(photos, key=lambda p: p.ts):
        t = (p.ocr_text or "").strip()
        if t and t not in seen:
            seen.append(t[:60])
        if len(seen) >= 3:
            break
    return "；".join(seen) if seen else None


def _pick_cover(photos: list, level: int = 2) -> str | None:
    """封面图选择（B3-4）：人脸优先 + 画面质量分

    L2：时间居中（离事件时间中点最近，非第一张）
    L3：独立封面，不做时间居中（长期流跨度大，取最新一次）
    排序键：是否含人脸（降）→ 质量分（降，默认 0.5）→ 时间键（L2 居中 / L3 最新）
    """
    candidates = [p for p in photos if getattr(p, "id", None)]
    if not candidates:
        return None
    if level >= 3:
        mid_ts = None
    else:
        ts = [p.ts for p in candidates if getattr(p, "ts", None)]
        mid_ts = (min(ts) + (max(ts) - min(ts)) / 2) if ts else None

    def _key(p):
        face = 1 if (p.face_count or 0) > 0 else 0
        quality = float(p.quality) if p.quality is not None else 0.5
        if level >= 3:
            ts_key = p.ts.timestamp() if getattr(p, "ts", None) else 0.0   # L3：最新优先
        elif mid_ts is not None and getattr(p, "ts", None):
            ts_key = -abs((p.ts - mid_ts).total_seconds())                # L2：时间居中
        else:
            ts_key = 0.0
        return (face, quality, ts_key)

    return max(candidates, key=_key).id
