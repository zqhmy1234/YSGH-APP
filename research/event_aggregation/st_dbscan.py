"""ST-DBSCAN 时空聚类（B3 L0 瞬间层核心）

语义：AND 语义邻居——两点相邻当且仅当 时间差 ≤ ε_t 且 空间距离 ≤ ε_s。
- 空间距离：Haversine（米）
- 复杂度 O(n²)，500 张秒级；万级需网格索引（原型注释，UTS 实现时优化）
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime


@dataclass
class Photo:
    """照片时间点（预处理后）"""

    id: str
    ts: datetime
    lat: float | None = None    # None = 无 GPS
    lng: float | None = None
    burst_group: int | None = None   # 连拍折叠组（预处理填充）
    tags: list[str] = None           # 标签（预处理透传，L2/L3 归并用）


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """两点球面距离（米）"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _is_neighbor(a: Photo, b: Photo, eps_t_sec: float, eps_s_m: float) -> bool:
    """AND 语义：时间与空间都近才算邻居；任一无 GPS 则只看时间"""
    dt = abs((a.ts - b.ts).total_seconds())
    if dt > eps_t_sec:
        return False
    if a.lat is None or a.lng is None or b.lat is None or b.lng is None:
        return True  # 无 GPS 按时间窗归组（B3 矩阵 #6）
    return haversine_m(a.lat, a.lng, b.lat, b.lng) <= eps_s_m


def st_dbscan(
    photos: list[Photo],
    eps_t_sec: float = 3600.0,     # 60min（B3-2：30min 保留为保守模式开关）
    eps_s_m: float = 500.0,        # 500m（B3-2）
    min_pts: int = 3,              # B3-2：<3 张散片进 L1 日卡片
) -> list[list[Photo]]:
    """返回簇列表；散片（噪声）不返回（由调用方并入 L1）"""
    n = len(photos)
    visited = [False] * n
    clusters: list[list[Photo]] = []

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        seed = photos[i]
        # 邻域查询（AND 语义）
        neighbors = [j for j in range(n) if j != i and _is_neighbor(seed, photos[j], eps_t_sec, eps_s_m)]

        if len(neighbors) < min_pts - 1:
            continue  # 噪声：散片 → L1 日卡片（B3-2 min_pts=3）

        # 密度可达扩展
        cluster: list[Photo] = [seed]
        queue = list(neighbors)
        while queue:
            j = queue.pop()
            if visited[j]:
                continue
            visited[j] = True
            cluster.append(photos[j])
            j_neighbors = [k for k in range(n) if k != j and _is_neighbor(photos[j], photos[k], eps_t_sec, eps_s_m)]
            if len(j_neighbors) >= min_pts - 1:
                queue.extend(k for k in j_neighbors if not visited[k])

        clusters.append(cluster)

    return clusters


def l1_daily_aggregate(clusters: list[list[Photo]], noise: list[Photo]) -> list[dict]:
    """L1 日聚合：簇 + 散片 → 自然日卡片

    规则（B3-2）：自然日 0-24 时；深夜 23:30-1:00 连续拍摄归属前一天。
    输出：[{date, photos: [...], is_sparse}]，稀疏（1-2 张）标记并入日卡片（B3 #8）。
    """
    def bucket_day(ts: datetime) -> str:
        if ts.hour == 23 and ts.minute >= 30:
            return ts.replace(hour=0, minute=0, second=0).date().isoformat()
        return ts.date().isoformat()

    days: dict[str, list[Photo]] = {}
    for cl in clusters:
        for p in cl:
            days.setdefault(bucket_day(p.ts), []).append(p)
    for p in noise:
        days.setdefault(bucket_day(p.ts), []).append(p)

    result = []
    for day, ps in sorted(days.items()):
        ps.sort(key=lambda p: p.ts)
        result.append({"date": day, "photos": ps, "is_sparse": len(ps) <= 2})
    return result
