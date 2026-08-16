"""四层事件聚合管线（B3）：预处理 → L0 → L1 → L2/L3 占位

管线分置（B3-6）：
  预处理（去重/连拍折叠/漂移修正）→ 端侧
  L0/L1（ST-DBSCAN + 日聚合）→ 端侧（30s 验收依赖，不依赖网络）
  L2/L3（LLM 归并/标签流）→ 云侧（原型占位，输出候选供后续实现）
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .st_dbscan import Photo, l1_daily_aggregate, st_dbscan

# 速度校验上限（B3-4）：步行 6km/h、驾车 120km/h；超限标记"坐标存疑"
WALK_SPEED_MS = 6000 / 3600.0
DRIVE_SPEED_MS = 120000 / 3600.0

# 连拍折叠（B3-2 #7）：<5s 间隔折叠为 1 个时间点
BURST_GAP_SEC = 5.0

# L0 参数（B3-2 已收敛）
L0_EPS_T_SEC = 3600.0    # 60min 默认；30min 为保守模式开关
L0_EPS_S_M = 500.0
L0_MIN_PTS = 3

# L1 深夜归属（B3-2）：23:30-1:00 连续拍摄归属前一天
NIGHT_HOUR, NIGHT_MIN = 23, 30


@dataclass
class RawPhoto:
    """原始照片（预处理输入）"""

    id: str
    ts: datetime
    lat: float | None = None
    lng: float | None = None
    tags: list[str] = field(default_factory=list)      # 腾讯云标签（预处理时已有）
    ocr_text: str | None = None                        # OCR 摘要
    source: str = "app"


@dataclass
class AggregateResult:
    """聚合输出"""

    l0_clusters: list[list[Photo]]
    l1_days: list[dict]
    l2_candidates: list[dict]      # 云侧 LLM 归并输入（原型占位）
    l3_candidates: list[dict]      # 标签主题流（原型占位）
    stats: dict


def preprocess(photos: list[RawPhoto]) -> list[Photo]:
    """预处理：连拍折叠 + GPS 漂移修正（B3-4）

    连拍折叠：<5s 间隔折叠为 1 个时间点（保留首张，id 记 burst 组）
    漂移修正：相邻照片速度超物理上限 → 单点取众数拉回 / 系统性偏移降级不猜
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
            folded.append(Photo(id=p.id, ts=p.ts, lat=p.lat, lng=p.lng, burst_group=group, tags=p.tags))
        prev_ts = p.ts

    # --- GPS 漂移修正（速度校验）---
    corrected: list[Photo] = []
    for i, p in enumerate(folded):
        if p.lat is None or p.lng is None:
            corrected.append(p)
            continue
        drift = False
        if i > 0 and corrected:
            prev = corrected[-1]
            if prev.lat is not None and prev.lng is not None:
                dt = (p.ts - prev.ts).total_seconds()
                if dt > 0:
                    from .st_dbscan import haversine_m
                    speed = haversine_m(prev.lat, prev.lng, p.lat, p.lng) / dt
                    if speed > DRIVE_SPEED_MS:
                        drift = True  # 坐标存疑：单点漂移由调用方取众数；此处仅标记
        if drift:
            corrected.append(Photo(id=p.id, ts=p.ts, lat=None, lng=None, burst_group=p.burst_group, tags=p.tags))
        else:
            corrected.append(p)
    return corrected


def aggregate(photos: list[RawPhoto], eps_t_sec: float = L0_EPS_T_SEC) -> AggregateResult:
    """完整聚合管线"""
    pts = preprocess(photos)

    # L0 瞬间层
    clusters = st_dbscan(pts, eps_t_sec=eps_t_sec, eps_s_m=L0_EPS_S_M, min_pts=L0_MIN_PTS)
    clustered_ids = {p.id for cl in clusters for p in cl}
    noise = [p for p in pts if p.id not in clustered_ids]

    # L1 日聚合
    days = l1_daily_aggregate(clusters, noise)

    # L2 候选（云侧占位）：跨 L0 簇的语义归并候选（B3：跨天 + 地点域连续或标签一致）
    # 原型实现：按主导标签分组，组内跨天≥2天且≥10张 → L2 候选（LLM 最终裁决在云侧）
    from collections import defaultdict

    tag_groups: dict[str, list] = defaultdict(list)
    for cl in clusters:
        hint = _tag_hint(cl)
        key = hint[0] if hint else "__no_tag__"
        tag_groups[key].append(cl)

    l2_candidates = []
    for tag, cls in tag_groups.items():
        merged = [p for cl in cls for p in cl]
        if _span_days(merged) >= 2 and len(merged) >= 10:   # B3-2 L2 最小规模
            l2_candidates.append(
                {
                    "cluster": [p.id for p in merged],
                    "tag": tag,
                    "time_range": [
                        min(p.ts for p in merged).isoformat(),
                        max(p.ts for p in merged).isoformat(),
                    ],
                    "place_hint": _place_hint(merged),
                    "tag_hint": hint,
                }
            )

    # L3：同标签 7 天内 ≥3 次（跨天）→ 主题流候选（B3-2）
    tag_count: dict[str, int] = {}
    for p in photos:
        for t in p.tags:
            tag_count[t] = tag_count.get(t, 0) + 1
    l3_candidates = [
        {"tag": t, "count": c}
        for t, c in tag_count.items()
        if c >= 3
    ]

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


def _span_days(cl: list[Photo]) -> int:
    if not cl:
        return 0
    days = max(p.ts.date() for p in cl) - min(p.ts.date() for p in cl)
    return days.days + 1


def _place_hint(cl: list[Photo]) -> str | None:
    """地点提示：众数 GPS 反查（原型只返回坐标簇中心）"""
    pts = [(p.lat, p.lng) for p in cl if p.lat is not None and p.lng is not None]
    if not pts:
        return None
    lat = sum(p[0] for p in pts) / len(pts)
    lng = sum(p[1] for p in pts) / len(pts)
    return f"{lat:.5f},{lng:.5f}"


def _tag_hint(cl: list[Photo]) -> list[str]:
    from collections import Counter
    tags = [t for p in cl for t in (p.tags or [])]
    return [t for t, _ in Counter(tags).most_common(3)]
