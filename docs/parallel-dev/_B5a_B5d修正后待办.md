# B5a/B5d 修正后待办（PR #1 审后 · 2026-08-26）

> 依据：PR #1（codex/asr-pipeline-hardening，已 merge 进 develop）实际代码 vs 原 audit（audit_B5a_B5d_voice.md）。
> 结论：**原 audit 中约 2/3 的 B5a 项已被 PR #1 解决**；以下仅列修正后仍待办项。
>
> ⚠️ **2026-09-23 逐项验码复核**：本表写于 08-26，此后 **J-1/J-2/J-3/J-4/J-5 五项均已实现（J-2/J-3 含专项测试）**，
> J-6 的"`notify.py` 零调用方"亦不成立 ⇒ 原"未闭环"描述**与代码不符**（陈旧登记）。下表已按证据逐项更正；
> **J-7（录音中断状态机 POC）未复核，仍按待办看待**。
> **教训**：待办文档会与代码分叉——**引用前必须验码**。我自己在 09-23 的排期里曾据本表把 J-2/J-3 写成"待做"（已同步更正），
> 这正是本项目反复栽的"信文档不信代码"。

## 已被 PR #1 解决（不再开发，仅验证）

| 原 audit 项 | PR #1 落点 |
|---|---|
| FIX-2 客户端语音链路断裂 | pipeline `_materialize_voice_audio`（COS 下载/本地路径）+ voice 状态机（succeeded/no_speech/failed）+ `_set_emotion_enrichment`；对象存储允许长 WAV 进 VAD，API 直传保持 8MB 上限 |
| FIX-3 SenseVoice 账号不可用 | 本地 SenseVoiceSmall-ONNX 退化方案（设计允许）+ CTC logits 置信度 + `should_enhance_with_local_emotion(result, mode=off/always/auto)` 开关（auto 尊重未来主通道情绪——review 点 5 已落实）+ `prepare_sensevoice.py` 预置脚本（review 点 3） |
| numpy/funasr-onnx/modelscope/imageio-ffmpeg 依赖 | requirements.txt 已显式加入（review 点 2） |
| workspace_id URL 硬编码 | 已改 `https://{workspace_id}.{settings.dashscope_region}.maas.aliyuncs.com`（review 点 4） |
| 人声检测兜底（数字静音） | `_wav_is_digital_silence` → `_no_speech_result`（no_speech 状态） |
| 长录音 VAD 分段 + 失败重试 | `transcribe`：WAV >5min 分段逐段转写合并，`ASR_PARTIAL_FAILURE` 重试标记 |
| 情绪置信度门控 | `emotion_actionable = emotion != 平静 and confidence >= 0.7`（EMOTION_ACTION_THRESHOLD） |
| 情绪落库字段 | `content.emotion = {emotion, confidence, source, model, actionable}` + `enrich_content_emotion` 低优先级 RQ 任务 |

## 修正后仍待办（进入 Wave 4 Agent J / Agent K）

| # | 待办 | 说明 |
|---|---|---|
| J-1 | ~~音频事件 12 类取 3（笑声/静音/键盘环境音）读取与消费~~ **✅ 已实现（09-23 复核）** | `asr/emotion.py` 消费 `AUDIO_EVENT_LAUGHTER`（笑声提为"开心"）/`AUDIO_EVENT_SILENCE`（静音提示）经 `apply_audio_event_effects`；`transcriber.py:249-254` 跨段**去重并集**；`models.py:84-86` 三标记字段 |
| J-2 | ~~噪音降权（SNR 检测，噪音大时 audio 权重降为持平）~~ **✅ 已实现（含专项测试）** | `asr/audio.py:estimate_snr`（帧能量分位数法）+ `NOISE_SNR_THRESHOLD_DB` + `emotion.py:_noise_weight`；接线 `transcriber.py:162-164`（**仅 WAV 参与**，压缩格式维持 high）；测试 `tests/test_asr.py:676-713`（干净→`high`／白噪→`equal`／非 16bit 或全零→`None` 不降权） |
| J-3 | ~~段级情绪合并对齐（时长最长段主导 + 峰值保留标记）~~ **✅ 已实现（含专项测试）** | `asr/emotion.py:merge_segment_emotion` 输出 `dominant`(时长最长段) + `peak`(置信度最高段) + `segments` + `strategy=longest_dominant_peak`；接线 `transcriber.py:230-232`；单段路径 `single_segment_emotion_merge`；测试 `tests/test_asr.py:716-756`（含 B5a §3 的"4 分钟平静 + 30 秒哽咽 → 主导平静、峰值难过"示例） |
| J-4 | ~~客户端长录音入口（>5min）~~ **✅ 描述已过期** | `uni_modules/yishu-recorder/utssdk/app-android/index.uts:44`：`MAX_RECORD_MS = 1800000` ＝ **30 分钟**（非原述 3.5min）；服务端长 WAV 分段（`_segments_for`）已就绪 |
| J-5 | ~~events.emotion 写入（事件层情绪消费）~~ **✅ 已实现** | `pipeline_ext/emotion.py:_emotion_merge_from_content`（:37 定义）被 `:74` 调用，`:93 event.emotion = current` 落库（对齐 B5a §3 dominant/peak 结构） |
| J-6 | ~~情绪关怀分层触发 + notify.py 零调用方占位~~ **⚠️ 部分过期（未全验）** | `notify.py` **已有真实调用方**：`pipeline_ext/emotion.py:112 maybe_notify_voice_done`、`:119 maybe_send_emotion_care`、`echo.py:306 create_message` ⇒ "零调用方占位"**不成立**。**未验部分**：分层触发条件（SAD/ANGRY/深夜/频次递减/<0.7 不触发）与产品部文案库是否落地，需细读 `notify.py` 后另判 |
| J-7 | 录音中断状态机集成 UTS（RECORDING→INTERRUPTED→恢复/30min 自动结束；POC 服务按 wav 16k 契约重做） | POC（research/poc）仅 start/stop、m4a 格式与后端 wav 契约不符、未集成 client；POC-02 文档声称与事实不符需修正 |
| K-1 | Android 前台服务（microphone/dataSync 互斥、短命化）+ WorkManager 队列（P0-P4、WiFi 约束）+ attribution tag 落地 | 全仓零实现；attribution tag 清单：sync_photo / voice_transcribe / event_aggregate / profile_fetch |
| K-2 | ASR 适配器抽象化（配置化 max_duration、Hy ASR/讯飞通道） | 当前通道字典 + 写死常量 MIN/MAX_SEG_S；低优先 |
| J-8 | FunASR 主通道情绪输出验证（若未来返回情绪则跳过本地） | `should_enhance_with_local_emotion` auto 模式已预留；等主通道 API 演进，登记验证项 |

## 任务卡归属

- J-1 ~ J-6、J-8 → Wave 4 Agent J（B5a 客户端/消费域）
- J-7 → Wave 4 Agent J（录音插件域，与 K-1 的 uni_modules 域相邻，J 管录音插件、K 管相册监听插件+新后台插件，文件不重叠）
- K-1、K-2 → Wave 4 Agent K（B5d Android 域）
- 原 audit B5d-15（Windows 定时）→ 随桌面端移出 MVP，不排期
