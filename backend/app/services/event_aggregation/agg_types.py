"""四层事件聚合 · 类型契约与阈值配置（B3）

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
原 pipeline.py 615 行同时承担"契约 + 预处理 + 候选构造 + 入口"四职责，
本模块收敛其中的**共享契约**，供其余三个模块单向依赖（消除循环依赖风险）：

  - 阈值常量（速度上限 / GPS 网格 / L2 地点域 / L3 主题流与生命周期 / L0 / L1 深夜）
  - `AGG_CONFIG` 统一参数源（AGG-016 端云阈值一致性）
  - `RawPhoto`（预处理输入）/ `AggregateResult`（聚合输出）两个 dataclass

⚠️ AGG-016 契约：`AGG_CONFIG` 与端侧 `client/utils/agg/agg_config.uts` 参数一一对应，
改参数须两端同步改（对照表见 `client/utils/agg/agg_config.md`）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from app.services.event_aggregation.st_dbscan import Photo

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
L0_EPS_T_SEC = 3600.0    # 60min 默认
L0_EPS_T_SEC_CONSERVATIVE = 1800.0  # 30min 保守模式（对齐端侧 agg_config.uts）
CONSERVATIVE_MODE = False           # 产品验收口径 L0 可调参数（与端侧 CONSERVATIVE_MODE 对齐）
L0_EPS_S_M = 500.0
L0_MIN_PTS = 3

# L1 深夜归属（B3-2）：23:30-1:00 连续拍摄归属前一天
NIGHT_HOUR, NIGHT_MIN = 23, 30

# 统一参数配置（AGG-016 端云阈值一致性：端侧/云侧从同一配置源取参）
# R1#13 契约同步：与端侧 client/utils/agg/agg_config.uts 参数一一对应
# （对照表见 client/utils/agg/agg_config.md，改参数两端同步改）。
AGG_CONFIG = {
    "l0": {
        "eps_t_sec": L0_EPS_T_SEC,                      # 端侧 L0_EPS_T_SEC_DEFAULT
        "eps_t_sec_conservative": L0_EPS_T_SEC_CONSERVATIVE,  # 端侧 L0_EPS_T_SEC_CONSERVATIVE
        "eps_s_m": L0_EPS_S_M,                          # 端侧 L0_EPS_S_M
        "min_pts": L0_MIN_PTS,                          # 端侧 L0_MIN_PTS
        "conservative_mode": CONSERVATIVE_MODE,  # 2026-08-26 集成接线：对齐端侧 agg_config.uts（AGG-016 双跑覆盖）
    },
    "burst_gap_sec": BURST_GAP_SEC,                     # 端侧 BURST_GAP_SEC
    "gps_speed": {"walk_ms": WALK_SPEED_MS, "drive_ms": DRIVE_SPEED_MS},  # 端侧 WALK_SPEED_MS/DRIVE_SPEED_MS
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
