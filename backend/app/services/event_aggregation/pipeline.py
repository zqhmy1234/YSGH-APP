"""四层事件聚合管线（B3）：预处理 → L0 → L1 → L2/L3 候选

管线分置（B3-6）：
  预处理（去重/连拍折叠/漂移修正）→ 端侧
  L0/L1（ST-DBSCAN + 日聚合）→ 端侧（30s 验收依赖，不依赖网络）
  L2/L3（LLM 归并/标签流）→ 云侧（本文件输出候选，供 llm_ops/event_merge 裁决）

Wave2-AgentD 增量（对照 audit_B3_events.md）：
  - B3-3 GPS 漂移：启用 WALK_SPEED_MS 阈值 + 单点众数纠正 + 系统性降级"不猜"
  - B3-2 L2 触发：跨天 + 地点域连续（5km/12hr）或标签一致，≥2 天 ≥10 张
  - B3-2 L3：7 天滑动窗口 ≥3 次（跨天）成流 + 生命周期状态机（活跃→静默→归档）
  - B3-4 封面：人脸优先 + 质量分 + 时间居中（L2）/ 不居中（L3）
  - B3-6 增量："先匹配后分裂"（新照片先并入现有簇，超限才独立成簇）
  - B3 #6 OCR 内容维：候选携带 ocr_summary 供 LLM（无 GPS 照片主信号）

波D ①（2026-09-10）结构拆分（**只搬代码不改逻辑**，615 行 → 4 文件）：
  - `agg_types.py`      阈值常量 + AGG_CONFIG + RawPhoto/AggregateResult（端云共享契约）
  - `agg_preprocess.py` 预处理（连拍折叠 / GPS 漂移修正）+ GPS 网格众数工具
  - `agg_candidates.py` L2/L3 候选构造 + 生命周期状态机 + 共享叶子工具
  - `pipeline.py`（本文件）管线入口：aggregate / incremental_aggregate / 增量吸收

本文件对上述三名做**全量 re-export**，`app.services.event_aggregation.pipeline.<name>`
这条既有导入路径保持可用（消费方零改动）：`app/services/events/aggregate.py`
（RawPhoto / aggregate / incremental_aggregate / l2_candidates / l3_candidates）、
`app/services/events/aggregate_write.py`（AggregateResult）、`app/api/events.py`（l3_lifecycle）、
本包 `run_validation.py`（AGG_CONFIG / preprocess 等）、`tests/test_agg_reference.py`。
"""
from __future__ import annotations

from datetime import timedelta

# 波D ①（2026-09-10）re-export：以下 import 同时承担"本模块自用"与"对外兼容再导出"两职，
# 故整块标注 noqa: F401（部分符号本文件不直接引用，仅用于维持既有导入路径）。
from app.services.event_aggregation.agg_candidates import (  # noqa: F401
    _l2_candidates,
    _l3_candidates,
    _make_l2_candidate,
    _max_in_window,
    _ocr_summary,
    _pick_cover,
    _place_continuous_groups,
    _place_hint,
    _span_days,
    _tag_hint,
    l3_lifecycle,
)
from app.services.event_aggregation.agg_preprocess import _cell, _cell_center, _grid_mode, preprocess  # noqa: F401
from app.services.event_aggregation.agg_types import (  # noqa: F401
    AGG_CONFIG,
    BURST_GAP_SEC,
    CONSERVATIVE_MODE,
    DRIVE_SPEED_MS,
    GPS_MODE_GRID_DECIMALS,
    GPS_MODE_MIN_SUPPORT,
    L0_EPS_S_M,
    L0_EPS_T_SEC,
    L0_EPS_T_SEC_CONSERVATIVE,
    L0_MIN_PTS,
    L2_MAX_GAP_HOURS,
    L2_MAX_GAP_KM,
    L3_ACTIVE_DAYS,
    L3_MIN_COUNT,
    L3_SILENT_DAYS,
    L3_WINDOW_DAYS,
    NIGHT_HOUR,
    NIGHT_MIN,
    WALK_SPEED_MS,
    AggregateResult,
    RawPhoto,
)
from app.services.event_aggregation.st_dbscan import Photo, haversine_m, l1_daily_aggregate, st_dbscan


def l0_eps_t_sec() -> float:
    """L0 时间窗取值：保守模式 1800s / 默认 3600s（对齐端侧 agg_config.uts l0EpsTsec()）"""
    return L0_EPS_T_SEC_CONSERVATIVE if AGG_CONFIG["l0"].get("conservative_mode") else L0_EPS_T_SEC


def aggregate(photos: list[RawPhoto], eps_t_sec: float | None = None) -> AggregateResult:
    """完整聚合管线（全量：新用户冷启动 / 手动全量重跑）

    eps_t_sec 默认按 AGG_CONFIG["l0"]["conservative_mode"] 取（3600/1800），
    显式传参（测试/双跑）不覆盖。
    """
    if eps_t_sec is None:
        eps_t_sec = l0_eps_t_sec()
    pts = preprocess(photos)

    # L0 瞬间层
    clusters = st_dbscan(pts, eps_t_sec=eps_t_sec, eps_s_m=L0_EPS_S_M, min_pts=L0_MIN_PTS)
    clustered_ids = {p.id for cl in clusters for p in cl}
    noise = [p for p in pts if p.id not in clustered_ids]

    # L1 日聚合
    days = l1_daily_aggregate(clusters, noise)

    # L2/L3 候选（云侧占位）
    l2_candidates = _l2_candidates(clusters)
    l3_candidates = _l3_candidates(photos)

    stats = {
        "raw": len(photos),
        "preprocessed": len(pts),
        "l0_clusters": len(clusters),
        "l1_days": len(days),
        "noise_to_l1": len(noise),
        "l2_candidates": len(l2_candidates),
        "l3_candidates": len(l3_candidates),
    }
    return AggregateResult(
        l0_clusters=clusters,
        l1_days=days,
        l2_candidates=l2_candidates,
        l3_candidates=l3_candidates,
        stats=stats,
    )


def incremental_aggregate(
    previous: AggregateResult | None,
    new_photos: list[RawPhoto],
    eps_t_sec: float | None = None,
) -> AggregateResult:
    """增量聚合（B3-6：先匹配后分裂；AGG-015 已确认结构不漂移）

    eps_t_sec 默认按 AGG_CONFIG["l0"]["conservative_mode"] 取（3600/1800）。

    策略（B3-6 增量处理 / LibrePhotos 思路）：
    1. 首次调用（previous=None）→ 全量聚合
    2. **先匹配**：新照片先尝试并入现有簇（时间窗 ±ε_t + 地点邻近），旧簇结构保持超集
    3. **超限才分裂**：未匹配照片独立聚类成新簇（≥min_pts 才成簇，散片进 L1）
    4. L1 日卡片按日期合并（新日期追加，旧日期保留）
    5. L2/L3 候选基于全量标签重算（MVP 简化）
    """
    if eps_t_sec is None:
        eps_t_sec = l0_eps_t_sec()
    if previous is None:
        return aggregate(new_photos, eps_t_sec=eps_t_sec)

    pts = preprocess(new_photos)

    # ① 先匹配：并入现有簇（旧簇只增不减 = 不漂移）
    all_clusters = [list(cl) for cl in previous.l0_clusters]
    remaining: list[Photo] = []
    for p in pts:
        if any(_can_absorb(cl, p, eps_t_sec) for cl in all_clusters):
            _absorb_first(all_clusters, p, eps_t_sec)
        else:
            remaining.append(p)

    # ② 超限才分裂：未匹配照片独立聚类
    new_clusters = st_dbscan(remaining, eps_t_sec=eps_t_sec, eps_s_m=L0_EPS_S_M, min_pts=L0_MIN_PTS)
    all_clusters.extend(new_clusters)
    new_noise = [
        p for p in remaining
        if not any(p.id in {x.id for x in cl} for cl in new_clusters)
    ]

    # L1：按日期合并（照片级 union——同日新旧照片合并去重，旧照片绝不丢失）
    # 修复：原实现 day_map[d["date"]] = d 整体覆盖，同日增量会把旧照片从日卡片抹掉（审查 CRITICAL）
    new_days = l1_daily_aggregate(new_clusters, new_noise)
    day_map = {d["date"]: d for d in previous.l1_days}
    for d in new_days:
        if d["date"] in day_map:
            existing = day_map[d["date"]]
            seen = {p.id for p in existing["photos"]}
            merged = existing["photos"] + [p for p in d["photos"] if p.id not in seen]
            merged.sort(key=lambda p: p.ts)
            day_map[d["date"]] = {
                "date": d["date"],
                "photos": merged,
                "is_sparse": len(merged) <= 2,
            }
        else:
            day_map[d["date"]] = d
    merged_days = sorted(day_map.values(), key=lambda d: d["date"])

    # L2/L3：全量重算（MVP 简化；完整版按"新标签形成"触发）
    all_photos = _flatten_photos(all_clusters) + _flatten_photos([new_noise])
    l2 = _l2_candidates(all_clusters)
    l3 = _l3_candidates(all_photos)

    return AggregateResult(
        l0_clusters=all_clusters,
        l1_days=merged_days,
        l2_candidates=l2,
        l3_candidates=l3,
        stats={
            "raw": len(all_photos),
            "preprocessed": len(pts),
            "l0_clusters": len(all_clusters),
            "l1_days": len(merged_days),
            "noise_to_l1": len(new_noise),
            "l2_candidates": len(l2),
            "l3_candidates": len(l3),
        },
    )


def l2_candidates(clusters: list[list[Photo]]) -> list[dict]:
    """L2 候选公共入口（B3-6 云侧只跑 L2/L3；端侧 L0/L1 真值后供云侧调用）"""
    return _l2_candidates(clusters)


def l3_candidates(photos: list[RawPhoto]) -> list[dict]:
    """L3 候选公共入口（B3-6）"""
    return _l3_candidates(photos)


def _flatten_photos(clusters: list[list[Photo]]) -> list[RawPhoto]:
    """簇内 Photo 还原为 RawPhoto（供标签统计）

    Photo 与 RawPhoto 字段兼容（id/ts/lat/lng/tags），单层列表（散片）也可直接传入。
    """
    out: list[RawPhoto] = []
    for cl in clusters:
        for p in cl:
            out.append(
                RawPhoto(
                    id=p.id, ts=p.ts, lat=p.lat, lng=p.lng, tags=p.tags or [],
                    ocr_text=p.ocr_text, quality=p.quality, face_count=p.face_count,
                )
            )
    return out


def _can_absorb(cluster: list[Photo], p: Photo, eps_t_sec: float) -> bool:
    """新照片能否并入现有簇：时间在簇窗 ±ε_t 内，且与至少一个成员时空邻近（B3-6）"""
    if not cluster:
        return False
    min_ts = min(x.ts for x in cluster)
    max_ts = max(x.ts for x in cluster)
    if not (min_ts - timedelta(seconds=eps_t_sec) <= p.ts <= max_ts + timedelta(seconds=eps_t_sec)):
        return False
    return any(_neighbor_ok(x, p, eps_t_sec) for x in cluster)


def _neighbor_ok(a: Photo, b: Photo, eps_t_sec: float) -> bool:
    """与 st_dbscan._is_neighbor 同语义（AND：时间与空间都近；任一无 GPS 只看时间）"""
    if abs((a.ts - b.ts).total_seconds()) > eps_t_sec:
        return False
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return True
    return haversine_m(a.lat, a.lng, b.lat, b.lng) <= L0_EPS_S_M


def _absorb_first(clusters: list[list[Photo]], p: Photo, eps_t_sec: float) -> None:
    """把新照片并入第一个可吸收的现有簇"""
    for cl in clusters:
        if _can_absorb(cl, p, eps_t_sec):
            cl.append(p)
            return
