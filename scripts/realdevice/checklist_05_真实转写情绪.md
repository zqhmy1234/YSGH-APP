# Checklist 05 · 真实转写 / 情绪双通道真机链路（US-17/18/19，B 级升 A）

> 生成：2026-08-28｜归属：scripts/realdevice（Agent D1）｜Wave 3 真机执行
> 对应：报告 §6.3（B 级升 A）｜功能域：语音（真实录音 → FunASR 转写 + SenseVoice 情绪，**非 mock**）
> 相关代码：`backend/app/services/external/asr/backends.py`（_transcribe_funasr / _infer_sensevoice / emotion_confidence）、`backend/app/api/asr.py`（channel: funasr/sensevoice/local_vad/mock）、`client/pages/record/record.uvue`（转写 + 情绪标签展示）

---

## 1. 目的

**真实录音** → FunASR 转写 + SenseVoice 情绪（**非 mock**），验证转写文本与情绪标注在真机呈现。**B 级升 A 的判定依据**（US-17/18/19：转写 / 情绪标签 / 语音详情展示）。

## 2. 前置条件

- P1 本地后端在线 + **P2 DASHSCOPE key 真实档**（`MOCK_EXTERNAL_AI=false`；FunASR 走百炼，SenseVoice 本地 CPU 通道）。
- **SenseVoice 资产已预置**：`backend/.env` 配 `SENSEVOICE_MODEL_DIR`（或 `scripts/prepare_sensevoice.py` 已跑）——生产/开发请求路径不联网下载。
- P8 准备 **3 段 30–60s 真实录音**：平静 / 开心 / 低落各一，背景安静，**原文留存**（对照表用）；P3 nova 11 已装最新包 + USB 授权。

### 2.1 前置就绪自查（不满足则本清单标"待补"，勿硬跑）

- □ 后端真实档：确认 `MOCK_EXTERNAL_AI=false`；ASR 通道非 mock
- □ DASHSCOPE key 可用（FunASR 百炼通道）；SenseVoice 资产就绪（`SENSEVOICE_MODEL_DIR` 有效）
- □ 3 段录音原文稿已留存（各 30–60s，情绪对应明确）
- □ 设备在线：`Get-Device`；`KeepAwake`；麦克风权限已授权

## 3. 执行步骤

> 后端 ASR 日志在 uvicorn 控制台（`channel=funasr/sensevoice`、`emotion_confidence`）——采集时重定向到 `evidence/ck05_backend_<ts>.log`。

**Step 1 · 档位确认**
- 确认后端真实档 + SenseVoice 资产（见 2.1）。**若 mock 档 → 本清单直接标"待补"**。
- 采证：记录后端档位；`GrabLog 05 yishu`。

**Step 2 · 真机语音记录 3 段**
- 打开记录页 → 依次录 3 段（平静 / 开心 / 低落，各 30–60s，背景安静）。
- 每段录完等待转写提交。逐段记录编号（S1 平静 / S2 开心 / S3 低落）与原文。
- 采证：`Shot 05 1a`（录音中/转写中）；`GrabLog 05 voice`。

**Step 3 · 等待转写完成（确认真实通道）**
- 等待转写完成；**后端日志确认 `channel=funasr` 或 `channel=sensevoice` 真实调用（非 mock 标记）**。
- 采证：后端日志 `ck05_backend_<ts>.log`（关键词 `funasr`/`sensevoice`/`channel`/`emotion_confidence`）。

**Step 4 · 验证转写文本**
- 逐段打开语音详情/转写结果，与原文对照，**主观准确率评估**（语义可读、错字可接受）。
- 采证：`Shot 05 2a`（S1 转写）、`Shot 05 2b`（S2）、`Shot 05 2c`（S3）。

**Step 5 · 验证情绪标签**
- 确认每段情绪标签呈现且与录音情绪一致：
  - **平静基线 0.8741 置信度参照**（S1 应识别为平静/中性）；
  - S2 开心、S3 低落应能区分（情绪标签不同）。
- 采证：`Shot 05 3a`（情绪标签展示，含置信度若 UI 展示）。

**Step 6 · 语音详情页核对**
- 语音详情页同时核对**转写 + 情绪**展示完整（文本可读 + 情绪标签正确）。
- 采证：`Shot 05 4a`（语音详情页全景）。

## 4. 预期（可判定口径）

| 项 | 预期 | 判定口径 |
|---|---|---|
| 档位 | 后端日志**真实 funasr/sensevoice 调用**（非 mock） | 日志含 `channel=funasr|sensevoice`，**无 mock 标记** |
| 转写 | 转写文本可读、错字可接受（语义可还原原文） | 主观准确率记录（逐段），语义还原度可接受 |
| 情绪 | 情绪标签出现且与录音情绪一致；平静/开心/低落可区分 | S2≠S3 情绪标签；S1 平静（参照 0.8741 置信度基线） |
| 展示 | 语音详情页转写 + 情绪双展示 | 截图核对 |

## 5. 证据清单（证据三要素：①nova 11 + 日期时间 ②截图/日志路径 ③结果判定）

- 截图：`evidence/ck05_1a_<ts>.png`、`ck05_2a_<ts>.png`、`ck05_2b_<ts>.png`、`ck05_2c_<ts>.png`、`ck05_3a_<ts>.png`、`ck05_4a_<ts>.png`
- 后端日志：`evidence/ck05_backend_<ts>.log`（**funasr/sensevoice 真实调用记录**）
- 设备日志：`evidence/ck05_voice_<ts>.log`
- logcat/后端日志过滤关键词：`yishu` / `funasr` / `sensevoice` / `emotion` / `channel` / `transcribe` / `asr` / `emotion_confidence`
- 转写对照表（记录表或附件）：原文 vs 转写（S1/S2/S3）+ 情绪标签 + 置信度

## 6. 判定标准

- **✅ 通过**：转写可用（语义还原）+ 情绪一致（S1 平静 / S2 开心 / S3 低落可区分）+ 后端日志**真实双通道（非 mock）** → B 级升 A。
- **❌ 失败**：情绪错标（S2/S3 区分不出或标反，记录样本与置信度，供 C 批校准参考）；转写不可用（语义完全错乱）。
- **🟡 部分**：真实档 key 未就绪 → 本清单标"待补"；个别样本情绪不准但通道真实 → 记录样本与置信度，标注待校准。

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

- **真实档是硬前提**：`MOCK_EXTERNAL_AI=false` + DASHSCOPE key + SenseVoice 资产，否则升 A 证据不足（mock 转写标记出现 = 档位不对）。
- 情绪为**声学情绪标签**（SenseVoice，含 `emotion_confidence`），非 LLM 文本情绪——判定以声学标签与录音情绪一致性为准。
- 录音样本留档（可留存 wav 路径）便于 C 批情绪校准。
