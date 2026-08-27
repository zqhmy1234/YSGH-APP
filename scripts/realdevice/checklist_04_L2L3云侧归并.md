# Checklist 04 · L2/L3 云侧归并真机链路（US-06/07，B 级升 A）

> 生成：2026-08-28｜归属：scripts/realdevice（Agent D1）｜Wave 3 真机执行
> 对应：报告 §6.3（B 级升 A）｜功能域：事件聚合（云侧 L2 主题 / L3 流归并，**真实 qwen 非 mock**）
> 相关代码：`backend/app/services/events/aggregate.py`（mode="l2l3"，_write_upper_candidates，merge_verdict）、`backend/app/services/llm_ops/event_merge.py`（L2 LLM 裁决）、`client/utils/event_sync.ts`（事件上云）

---

## 1. 目的

**真机端到端**触发云侧 L2 主题 / L3 流归并（真实 qwen，非 mock），验证时间轴呈现（主题事件标题/封面/成员数、跨日分组正确、无重复/错并）。**B 级升 A 的判定依据**。

## 2. 前置条件

- P1 本地后端在线 + **P2 DASHSCOPE key 真实档**（`.env` 配 DASHSCOPE_API_KEY + DASHSCOPE_WORKSPACE_ID + `MOCK_EXTERNAL_AI=false`）。
- P5 跨日/跨场景照片组：**15–20 张分 2–3 天**（EXIF 拍摄时间跨日，如"出差 3 天"：Day1 5 张、Day2 6 张、Day3 5 张）；P3 nova 11 已装最新包 + USB 授权。

### 2.1 前置就绪自查（不满足则本清单标"待补"，勿硬跑）

- □ 后端真实档：确认 `backend/.env` `MOCK_EXTERNAL_AI=false`；后端启动日志无 mock 标记
- □ DASHSCOPE key 可用：`python scripts/check_dashscope.py`（或 api_smoke 真实档跑过）无 403
- □ RQ worker 在跑（`run_user_aggregation` 是 worker 任务，日志在 worker 控制台/日志文件）
- □ 设备在线：`Get-Device`；`KeepAwake`
- □ 跨日照片组已备（EXIF 拍摄时间跨 2–3 天）

## 3. 执行步骤

> 后端日志采集：`run_user_aggregation` / `merge_verdict` 在 **RQ worker 控制台**（`[yishu]`/aggregate 前缀）——采集时把 worker 输出重定向到 `evidence/ck04_backend_<ts>.log` 或截取控制台日志。

**Step 1 · 档位确认**
- 确认后端真实档（见 2.1）。**若为 mock 档 → 本清单直接标"待补"**（mock 不构成升 A 证据）。
- 采证：记录后端版本/档位；`GrabLog 04 yishu`。

**Step 2 · 真机导入跨日照片组**
- 注入/导入跨日照片组（scan_file 新目录 `/sdcard/Pictures/yishu_test4`，EXIF 拍摄时间跨 2–3 天）。
- 等待上传完成（logcat `found`/`batch received`）。
- 采证：`Shot 04 1a`（导入后时间轴/上传中）；`GrabLog 04 upload`。

**Step 3 · 等待云侧聚合管线触发**
- 等待 L2/L3 候选聚合（后端日志出现 `run_user_aggregation` / `L2/L3 候选` / `merge_verdict`）。
- **记录 T0 = 上传完成时刻**。
- 采证：后端日志 `ck04_backend_<ts>.log`（关键词 `aggregate`/`merge_verdict`/`L2`/`L3`）。

**Step 4 · 打开时间轴 → 验证 L2 主题事件**
- 打开首页时间轴，等待主题事件出现。
- 验证：**L2 主题卡片**（标题 = LLM 裁决 或 `主题 · {tag}（N 条）` 模板、封面、成员数）；**跨日分组正确**（成员跨日不散到单独日卡片）；无重复 / 错并。
- **记录 T1 = 主题事件出现时刻**；耗时 = T1 − T0。
- 采证：`Shot 04 2a`（L2 主题卡片截图）；`GrabLog 04 timeline`。

**Step 5 · L3 流（可选，若数据满足）**
- 若同一标签在 7 天窗 ≥3 次 → 验证 L3 主题流事件（后端日志 `l3_candidates`，时间轴 L3 展示）。
- 采证：`Shot 04 3a`（L3 流展示）；后端日志确认。

## 4. 预期（可判定口径）

| 项 | 预期 | 判定口径 |
|---|---|---|
| 档位 | 后端日志显示**真实 qwen 调用**（非 mock） | 日志含 `merge_verdict` 真实 LLM 请求，**无 mock 标记** |
| L2 归并 | 主题事件正确：标题/封面/成员数正确，成员跨日不散 | 时间轴 L2 卡片成员数 = 跨日照片数；无重复/错并 |
| L3 流 | 7 天窗 ≥3 次标签出主题流（若数据满足） | 后端 `l3_candidates` + 时间轴展示 |
| 耗时 | 真机触发 → 主题事件出现；**参考 10s 内出现候选（宽松观察项）** | 记录实际耗时，超时标注不判死 |

## 5. 证据清单（证据三要素：①nova 11 + 日期时间 ②截图/日志路径 ③结果判定）

- 截图：`evidence/ck04_1a_<ts>.png`、`ck04_2a_<ts>.png`（L2 主题卡片）、`ck04_3a_<ts>.png`（L3 流，可选）
- 后端日志：`evidence/ck04_backend_<ts>.log`（**event_merge 真实 qwen 调用记录，非 mock 标记**）
- 设备日志：`evidence/ck04_upload_<ts>.log`、`ck04_timeline_<ts>.log`
- logcat/后端日志过滤关键词：`yishu` / `run_user_aggregation` / `aggregate` / `L2` / `L3` / `merge_verdict` / `event_merge` / `qwen`
- 耗时记录：T0（上传完成）__ → T1（主题出现）__ = __s

## 6. 判定标准

- **✅ 通过**：归并正确（L2 标题/封面/成员数对、跨日不散、无重复/错并）+ 后端日志为**真实 qwen 调用（非 mock）** → B 级升 A。
- **❌ 失败**：出现错并 / 缺并（附后端日志定位 merge_verdict 输入输出）；或后端为 mock 档（不构成升 A 证据）。
- **🟡 部分**：跨日照片组数据不足（不满足跨日条件）→ 归并正确性标"待补"；耗时超 10s 仅标注，不判死。

## 7. 记录表

```markdown
## 记录表
- 执行日期：____ ｜ 设备：nova 11 ｜ 后端：本地/远程（地址____）｜ 档位：mock/真实
- 前置就绪：□ 后端在线 □ 网络（WiFi/蜂窝）□ 账号 □ 相册数据 □ 其他____
- 步骤结果：1)____ 2)____ 3)____ 4)____ 5)____ 6)____ 7)____（每步：通过/失败/备注）
- 证据文件：截图____ 日志____ 其他____
- 总体判定：✅ 通过 / ❌ 失败 / 🟡 部分（降级原因：____）
- 问题描述（失败时）：现象____ 期望____ 后端日志关键行____
```

## 8. 备注 / 降级

- **真实档是硬前提**：`MOCK_EXTERNAL_AI=false` + DASHSCOPE key 可用，否则本清单不达标（升 A 需真实 qwen 调用证据）。
- 后端日志在 RQ worker：确认 worker 进程在跑且输出可采集（uvicorn 日志不含 worker 任务详情）。
- 设备是日常机：测试照片用完清理（删除设备目录 + 清测试账号数据）。
