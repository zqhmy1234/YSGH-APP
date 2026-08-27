# 端云聚合参数契约（AGG-016 · R1#13 同步定稿）

> 单一来源纪律：端侧 `client/utils/agg/agg_config.uts` 与云侧
> `backend/app/services/event_aggregation/pipeline.py` 的 `AGG_CONFIG` **必须同值**。
> **改参数必须两端同步改**，并跑 `python scripts/gen_agg_fixtures.py` 重生成夹具 +
> 端侧 agg-check 双跑兜底（AGG-016）。对照表见下。

## 共享参数对照表（端云 L0/L1 同参）

| 语义 | 端侧常量（agg_config.uts） | 云侧键（pipeline.py AGG_CONFIG） | 值 | 说明 |
|---|---|---|---|---|
| L0 时间窗默认 | `L0_EPS_T_SEC_DEFAULT` | `l0.eps_t_sec` | 3600.0（60min） | 宽窗默认 |
| L0 时间窗保守 | `L0_EPS_T_SEC_CONSERVATIVE` | `l0.eps_t_sec_conservative` | 1800.0（30min） | 保守模式开关切到的窗 |
| L0 保守模式开关 | `CONSERVATIVE_MODE` | `l0.conservative_mode` | false | false=60min 宽窗；true=30min |
| L0 空间窗 | `L0_EPS_S_M` | `l0.eps_s_m` | 500.0（m） | |
| L0 min_pts | `L0_MIN_PTS` | `l0.min_pts` | 3 | <3 张散片进 L1 日卡片 |
| 连拍折叠阈值 | `BURST_GAP_SEC` | `burst_gap_sec` | 5.0（s） | <5s 间隔折叠为 1 时间点 |
| 步行速度上限 | `WALK_SPEED_MS` | `gps_speed.walk_ms` | 6000/3600（m/s） | 速度校验下限 |
| 驾车速度上限 | `DRIVE_SPEED_MS` | `gps_speed.drive_ms` | 120000/3600（m/s） | 漂移判定上限 |

## 云侧独有参数（端侧不参与，仅云侧 L2/L3 使用）

| 语义 | 云侧键（pipeline.py AGG_CONFIG） | 值 | 说明 |
|---|---|---|---|
| 深夜归属起点 | `night.hour` / `night.minute` | 23 / 30 | L1 深夜 23:30-1:00 归属前一天（端侧 st_dbscan 独立实现同规则） |
| L2 最小跨天数 | `l2_min_days` | 2 | |
| L2 最小照片数 | `l2_min_photos` | 10 | |
| L2 地点域连续 | `l2_place.max_gap_km` / `max_gap_hours` | 5.0 / 12.0 | |
| L3 主题流阈值 | `l3_tag_threshold` | 3 | 7 天窗 ≥3 次（跨天）成流 |
| L3 滑动窗口 | `l3_window_days` | 7 | |
| L3 生命周期 | `l3_lifecycle.active_days` / `archive_days` | 30 / 90 | |

## 同步纪律

1. **共享参数改动**：端侧 `agg_config.uts` + 云侧 `AGG_CONFIG` 同值同 commit；
   只改一侧会被 AGG-016 双跑（`scripts/gen_agg_fixtures.py` + 端侧 agg-check）抓出。
2. **双跑验证**：改参后重生成夹具 `python scripts/gen_agg_fixtures.py`
   （Python 参考端实算期望 → `client/utils/agg/fixtures.uts`），端侧 agg-check 页
   全绿才算同步完成。
3. **消费方**：
   - 端侧：`agg_runner.uts`（L0_EPS_S_M/L0_MIN_PTS/l0EpsTsec）、
     `agg/pipeline.uts`（BURST_GAP_SEC/DRIVE_SPEED_MS）、`agg_check.uts`
   - 云侧：`event_aggregation/pipeline.py`（`l0_eps_t_sec()` / `preprocess` / `aggregate`）、
     `scripts/gen_agg_fixtures.py`、`event_aggregation/run_validation.py`
