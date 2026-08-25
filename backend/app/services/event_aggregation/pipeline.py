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
"""
from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .st_dbscan import Photo, haversine_m, l1_daily_aggregate, st_dbscan

# 速度校验上限（B3-3）：步行 6km/h、驾车 120km/h；超限标记"坐标存疑"
WALK_SPEED_MS = 6000 / 3600.0
DRIVE_SPEED_MS = 120000 / 3600.0

# GPS 众数网格（约 0.001° ≈ 100m 格）：单点漂移按众数格中心拉回（中值滤波思想）
GPS_MODE_GRID_DECIMALS = 3
GPS_MODE_MIN_SUPPORT = 2

# L2 地点域连续（B3-2）：跨天 + 地点域连续（5km/12hr）或主题标签一致
L2_MAX_GAP_KM = 5.0
L2_MAX_GAP_HOURS = 12.0

# L3 主题流（B3-2）：同一标签 7 天内 ≥3 次（跨天）成流
L3_WINDOW_DAYS = 7
L3_MIN_COUNT = 3

# L3 生命周期（B3-2）：活跃（30 天）→ 静默降级 → 归档
L3_ACTIVE_DAYS = 30      # 最近活动 ≤30 天 → 活跃
L3_SILENT_DAYS = 90      # 静默 90 天无活动 → 归档

# 连拍折叠（B3-2 #7）：<5s 间隔折叠为 1 个时间点
BURST_GAP_SEC = 5.0

# L0 参数（B3-2 已收敛）
L0_EPS_T_SEC = 3600.0    # 60min 默认；30min 为保守模式开关
L0_EPS_S_M = 500.0
L0_MIN_PTS = 3

# L1 深夜归属（B3-2）：23:30-1:00 连续拍摄归属前一天
NIGHT_HOUR, NIGHT_MIN = 23, 30

# 统一参数配置（AGG-016 端云阈值一致性：端侧/云侧从同一配置源取参）
AGG_CONFIG = {
    "l0": {"eps_t_sec": L0_EPS_T_SEC, "eps_s_m": L0_EPS_S_M, "min_pts": L0_MIN_PTS},
    "burst_gap_sec": BURST_GAP_SEC,
    "night": {"hour": NIGHT_HOUR, "minute": NIGHT_MIN},
    "l2_min_days": 2,
    "l2_min_photos": 10,
    "l2_place": {"max_gap_km": L2_MAX_GAP_KM, "max_gap_hours": L2_MAX_GAP_HOURS},
    "l3_tag_threshold": L3_MIN_COUNT,
    "l3_window_days": L3_WINDOW_DAYS,
    "l3_lifecycle": {"active_days": L3_ACTIVE_DAYS, "archive_days": L3_SILENT_DAYS},
}


@dataclass
class RawPhoto:
    """原始照片（预处理输入）"""

    id: str
    ts: datetime
    lat: float | None = None
    lng: float | None = None
    tags: list[str] = field(default_factory=list)      # 腾讯云标签（预处理时已有）
    ocr_text: str | None = None                        # OCR 摘要（B3 #6 内容维）
    quality: float | None = None                       # 画面质量分 0-1（B3-4 封面）
    face_count: int | None = None                      # 人脸数（B3-4 封面人脸优先）
    source: str = "app"


@dataclass
class AggregateResult:
    """聚合输出"""

    l0_clusters: list[list[Photo]]
    l1_days: list[dict]
    l2_candidates: list[dict]      # 云侧 LLM 归并输入（含元数据/封面/OCR）
    l3_candidates: list[dict]      # 标签主题流（7 天窗 + 生命周期 + 独立封面）
    stats: dict


def preprocess(photos: list[RawPhoto]) -> list[Photo]:
    """预处理：连拍折叠 + GPS 漂移修正（B3-2 #7 / B3-3）

    连拍折叠：<5s 间隔折叠为 1 个时间点（保留首张，id 记 burst 组）
    漂移修正：速度校验（步行 6km/h / 驾车 120km/h 上限）→
      单点漂移取众数拉回（corrected）/ 系统性降级不猜（degraded）/ 移动中（approx）
    """
    photos = sorted(photos, key=lambda p: p.ts)

    # --- 连拍折叠 ---
    # 与原始序列的紧邻上一张比较（<5s 归入当前组），而非与折叠后末张比较
    folded: list[Photo] = []
    group = 0
    prev_ts: datetime | None = None
    for p in photos:
        if prev_ts is not None and (p.ts - prev_ts).total_seconds() < BURST_GAP_SEC:
            folded[-1].burst_group = group  # 归入当前折叠组
        else:
            group += 1
            folded.append(
                Photo(
                    id=p.id, ts=p.ts, lat=p.lat, lng=p.lng, burst_group=group,
                    tags=p.tags, ocr_text=p.ocr_text,
                    quality=p.quality, face_count=p.face_count,
                )
            )
        prev_ts = p.ts

    # --- GPS 漂移修正（B3-3）---
    # 众数位置（全批网格众数）：单点漂移拉回基准；系统性偏移整批一致 → 众数即批位置，不误伤
    mode_cell, mode_count = _grid_mode(folded)
    corrected: list[Photo] = []
    for i, p in enumerate(folded):
        if p.lat is None or p.lng is None:
            p.gps_state = "none"
            corrected.append(p)
            continue
        # 与原始序列紧邻上一张比较（漂移点不污染后续判断）
        speed = None
        if i > 0:
            prev = folded[i - 1]
            if prev.lat is not None and prev.lng is not None:
                dt = (p.ts - prev.ts).total_seconds()
                if dt > 0:
                    speed = haversine_m(prev.lat, prev.lng, p.lat, p.lng) / dt
        if speed is None or speed <= WALK_SPEED_MS:
            corrected.append(p)            # 步行速度内：坐标可信
            continue
        cell = _cell(p.lat, p.lng)
        if speed <= DRIVE_SPEED_MS:
            # 步行以上、驾车上限内：移动中/车辆（含跨城行程），精确 POI 存疑。
            # 不拉回——可能是真实行程，坐标保留仅标记 approx（B3-3 ①）
            p.gps_state = "approx"
            corrected.append(p)
            continue
        # 超过驾车物理上限 → 漂移嫌疑（人不可能，B3-3 ①）
        if mode_cell is not None and cell == mode_cell:
            corrected.append(p)            # 在众数位置（上一漂移点污染速度判断）→ 坐标可信
            continue
        if mode_cell is not None and mode_count >= GPS_MODE_MIN_SUPPORT:
            # 单点漂移（少数照片跳出事件簇）：取众数位置拉回（B3-3 ② 中值滤波思想）
            # 时间维照常参与；坐标近似为事件众数位置
            p.lat, p.lng = _cell_center(mode_cell)
            p.gps_state = "corrected"
        else:
            # 超物理上限且无众数可依：不猜精确坐标——坐标置空并标记 degraded，
            # 地点提示降级为"附近/某区"粒度（B3-3 降级不猜）
            p.lat = p.lng = None
            p.gps_state = "degraded"
        corrected.append(p)
    return corrected


def aggregate(photos: list[RawPhoto], eps_t_sec: float = L0_EPS_T_SEC) -> AggregateResult:
    """完整聚合管线（全量：新用户冷启动 / 手动全量重跑）"""
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
    eps_t_sec: float = L0_EPS_T_SEC,
) -> AggregateResult:
    """增量聚合（B3-6：先匹配后分裂；AGG-015 已确认结构不漂移）

    策略（B3-6 增量处理 / LibrePhotos 思路）：
    1. 首次调用（previous=None）→ 全量聚合
    2. **先匹配**：新照片先尝试并入现有簇（时间窗 ±ε_t + 地点邻近），旧簇结构保持超集
    3. **超限才分裂**：未匹配照片独立聚类成新簇（≥min_pts 才成簇，散片进 L1）
    4. L1 日卡片按日期合并（新日期追加，旧日期保留）
    5. L2/L3 候选基于全量标签重算（MVP 简化）
    """
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
    for a, b in zip(seq, seq[1:]):
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


def _cell(lat: float, lng: float) -> tuple[float, float]:
    """GPS 众数网格单元（约 0.001° ≈ 100m 格）"""
    return (round(lat, GPS_MODE_GRID_DECIMALS), round(lng, GPS_MODE_GRID_DECIMALS))


def _cell_center(cell: tuple[float, float]) -> tuple[float, float]:
    """网格单元中心（众数拉回目标位置；格内点收敛到同一格中心）"""
    return cell


def _grid_mode(folded: list[Photo]) -> tuple[tuple[float, float] | None, int]:
    """全批 GPS 众数（网格计数）：(众数格, 支持数)；无 GPS 或无众数返回 (None, 0)"""
    counts: dict[tuple[float, float], int] = {}
    for p in folded:
        if p.lat is not None and p.lng is not None:
            c = _cell(p.lat, p.lng)
            counts[c] = counts.get(c, 0) + 1
    if not counts:
        return None, 0
    cell = max(counts, key=counts.get)
    return cell, counts[cell]


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
