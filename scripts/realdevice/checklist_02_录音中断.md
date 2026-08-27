# Checklist 02 · 录音中断恢复（US-20/52）

> 生成：2026-08-28｜归属：scripts/realdevice（Agent D1）｜Wave 3 真机执行
> 对应：报告 §8.1④｜功能域：语音（录音中断状态机 + 30min 自动结束 + 前台服务说明）
> 相关代码：`client/uni_modules/yishu-recorder/utssdk/app-android/index.uts`（中断状态机 RECORDING→INTERRUPTED→恢复/暂停，30min 自动结束分段保存）、`client/pages/record/record.uvue`（onInterrupted 回调 + resumeRecordNow + auto-stop）、`client/utils/voice.ts`（MAX_RECORD_MS=1800000）、`client/uni_modules/yishu-photo-watch`（dataSync FGS 互斥）

---

## 1. 目的

验证来电/系统抢占打断 → **恢复续录不丢段**（状态机 resume）；**30min 自动结束**并保存已录内容；前台服务（FGS）在录音/监听场景的存活与互斥表现。本清单只管**中断状态机**（转写 mock/真实均可）。

## 2. 前置条件

- P1 本地后端在线（mock 或真实档均可）；P3 nova 11 已装最新包 + USB 授权；麦克风权限已授权。
- **P7 通话模拟方式**：第二设备呼入为最优先；不可用则备选（README §5）：
  - 系统闹钟（设定 1 分钟后响铃，闹钟响铃抢占麦克风触发 `onInterruptionBegin`）
  - 播放媒体/语音通话类应用抢占（播放音乐/视频时系统可能抢占麦克风）

### 2.1 前置就绪自查（不满足则本清单标"待补"，勿硬跑）

- □ 后端在线：`curl http://127.0.0.1:8000/healthz` → `{"status":"ok"}`
- □ 设备在线：`Get-Device`；`KeepAwake`
- □ 麦克风权限：App 设置 → 权限 → 麦克风已开启
- □ 打断模拟方式已备（呼入电话 / 闹钟 / 媒体播放）

## 3. 执行步骤

> 开始前 `Clear-Logcat`；动态场景可 `Record 02 int 30` 录屏补强。

**Step 1 · 开始录音（基线样本）**
- 打开记录页 → 点录音按钮 → 先说出**第一段明确内容**（如"第一段，我在公司"）约 10s。
- 采证：`Shot 02 1a`（录音中 UI）；`GrabLog 02 record`。

**Step 2 · 模拟打断（~10s 后）**
- 触发打断：第二设备呼入 / 闹钟响铃 / 媒体抢占。
- **预期**：录音自动暂停；UI 提示 `录音被中断，已暂停`；logcat `[yishu] record interrupted (autoPaused=true)`；recorder 状态 `REC_STATE_INTERRUPTED`。
- 采证：`Shot 02 2a`（中断暂停 UI）；`GrabLog 02 interrupt`。

**Step 3 · 恢复续录**
- 打断结束（挂断 / 闹钟关闭 / 停止媒体）。
- **预期**：`shouldResume=true` 自动恢复（状态机 INTERRUPTED→RECORDING，logcat 有恢复日志）；若未自动恢复，点页面 `resumeRecordNow()` 手动恢复（hint `已继续录音`）。
- 继续说出**第二段明确内容**（如"第二段，我在家"）约 20s。
- 采证：`Shot 02 3a`（恢复后录音中）；`GrabLog 02 record`。

**Step 4 · 结束并验证音频完整**
- 点停止 → 转写/分段保存流程正常（mock 或真实均可）。
- **预期**：`durationMs` ≈ 两段合计（精确计时**排除**中断/暂停时段）；无"录音太短"或丢失提示；转写文本同时包含两段内容（第二段内容应存在，证明续录未丢段）。
- 采证：`Shot 02 4a`（停止后转写结果/详情）；`GrabLog 02 voice`。

**Step 5 · 30min 自动结束**
- 方案 A（真实，耗时长）：开始录音后不操作，等待到点 → **预期** logcat `[yishu] record auto-stop at 30min, saving segment: <ms>`，已录内容自动分段保存并提交。
- 方案 B（调试参数缩短，推荐省时）：临时把 `client/uni_modules/yishu-recorder/utssdk/app-android/index.uts` 的 `MAX_RECORD_MS` 改小（如 60000），验证同一 `onAutoStop` 路径；**验证后还原常量为 1800000** 并在记录表标注"调试参数缩短验证"。
- **预期**：到点自动结束 + 已录内容保存（分段文件存在 / 提交成功），不丢。
- 采证：`GrabLog 02 autostop`（`auto-stop` 关键词）；`Shot 02 5a`（自动结束提示）。

**Step 6 · FGS 通知观察**
- 录音期间检查通知栏：标准基座下录音用系统 RecorderManager，**无 microphone FGS 通知（属预期）**；若看到 dataSync 通知（`忆述光华 · 数据同步中`）在录音开始时消失 → 为 microphone/dataSync **互斥正常**（B5d §3）。
- microphone FGS 常驻的**真实验证归 checklist 07**（自定义基座 + POST_NOTIFICATIONS）。
- 采证：`Shot 02 6a`（通知栏截图，注明可见/不可见）。

## 4. 预期（可判定口径）

| 步骤 | 预期 | 判定口径 |
|---|---|---|
| 2 | 中断自动暂停，UI/logcat 明确 | logcat 含 `record interrupted`；UI hint `录音被中断，已暂停` |
| 3 | 恢复后续录，不丢段 | durationMs≈两段合计；转写含两段内容；无丢失提示 |
| 4 | 停止后转写正常 | 转写文本可用（错字可接受），两段都在 |
| 5 | 30min 自动结束 + 保存 | logcat 含 `record auto-stop at 30min`；已录内容落盘/提交（可标注"调试参数缩短验证"） |
| 6 | FGS 说明清晰 | 通知栏截图 + 互斥说明（microphone FGS 归 07） |

## 5. 证据清单（证据三要素：①nova 11 + 日期时间 ②截图/日志路径 ③结果判定）

- 截图：`evidence/ck02_1a_<ts>.png`、`ck02_2a_<ts>.png`、`ck02_3a_<ts>.png`、`ck02_4a_<ts>.png`、`ck02_5a_<ts>.png`、`ck02_6a_<ts>.png`
- 日志：`evidence/ck02_record_<ts>.log`、`ck02_interrupt_<ts>.log`、`ck02_voice_<ts>.log`、`ck02_autostop_<ts>.log`
- 录屏（可选）：`evidence/ck02_int_<ts>.mp4`（中断/恢复动态过程）
- logcat 过滤关键词：`yishu` / `record` / `interrupt` / `voice` / `auto-stop`
- 时长对比记录：第一段 ___s + 中断时段 ___s + 第二段 ___s vs 停止回调 durationMs ___s

## 6. 判定标准

- **✅ 通过**：恢复续录 + 不丢段（Step 2–4）✅；30min 自动结束（Step 5）✅（可标注"调试参数缩短验证"）。
- **❌ 失败**：中断后无法恢复 / 丢段（durationMs 明显小于两段合计）/ 30min 不到点或自动结束丢内容（附 logcat 堆栈）。
- **🟡 部分**：无第二设备且闹钟/媒体备选也无法触发 `onInterruptionBegin` → 中断场景标"待补"，仅记录 30min 自动结束项。

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

- 30min 实测耗时长：优先方案 B（调试参数缩短）验证同路径，并在记录表明确标注，不虚构 30min 真实耗时。
- 中断模拟备选与真实来电差异：系统闹钟/媒体抢占同样触发 `onInterruptionBegin`，但"来电后自动恢复"行为以真实通话为最准；若备选触发路径不同，注明使用方式。
- 设备是日常机：测试录音用完清理（测试内容提交后删除该测试内容/测试用户，`unionid LIKE 'mock-unionid-%'`）。
