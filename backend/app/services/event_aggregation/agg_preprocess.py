"""四层事件聚合 · 预处理（B3-2 #7 连拍折叠 / B3-3 GPS 漂移修正）

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
本模块承载管线的**第一段**——原始照片 → 时间点序列（端侧执行，不依赖网络），
含 GPS 网格众数三件工具（`_cell` / `_cell_center` / `_grid_mode`），
仅被本模块 preprocess 使用，故随其一并搬迁。
"""
from __future__ import annotations

from datetime import datetime

from app.services.event_aggregation.agg_types import (
    BURST_GAP_SEC,
    DRIVE_SPEED_MS,
    GPS_MODE_GRID_DECIMALS,
    GPS_MODE_MIN_SUPPORT,
    WALK_SPEED_MS,
    RawPhoto,
)
from app.services.event_aggregation.st_dbscan import Photo, haversine_m


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
