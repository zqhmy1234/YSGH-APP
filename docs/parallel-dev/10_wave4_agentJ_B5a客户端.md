# Wave 4 · Agent J（B5a 客户端/消费域）任务卡——docs/parallel-dev/10

## Mission
按《_B5a_B5d修正后待办.md》完成 J-1~J-8：音频事件 3 类消费、噪音降权、段级情绪合并对齐、客户端长录音入口、events.emotion 联动、情绪关怀分层触发、录音中断状态机 UTS 集成、FunASR 情绪输出验证登记。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md` + `13_集成规则` + **`_B5a_B5d修正后待办.md`（必读，你的范围以它为准）**。
2. PR #1 已实现：多格式转写、本地 SenseVoice 情绪（置信度+开关）、VAD 分段、no_speech、pipeline 语音状态机、`enrich_content_emotion` RQ 任务——**不要再实现这些**。
3. 现状：`client/utils/voice.ts`（录音 3.5min 上限，start/stop 简版）；`backend/app/services/notify.py`（care_followup 占位、voice_done 零调用方）；`backend/app/services/external/asr.py`（PR #1 版）；`pipeline_ext/emotion.py`（你的钩子）；POC 录音服务 `research/poc/.../RecordingService.kt`（仅 start/stop、m4a、未集成）。
4. 设计依据：B5a §2 冲突合并、§3 段级合并（主导=时长最长段+峰值）、§5 情绪消费链路。

## Scope（可改）
1. `backend/app/services/external/asr.py`（**跨波次**：PR #1 已 merge 到 develop，你基于最新 develop 开发；只加音频事件/噪音/段级合并，不动主转写链）
2. `backend/app/services/pipeline_ext/emotion.py`（**你的钩子**：consume_emotion → events.emotion 联动）
3. `backend/app/services/notify.py`（**独占**：情绪关怀分层触发 + voice_done 接线 + 22:00 调度登记）
4. `client/utils/voice.ts`（长录音入口：上限放开 + 音频持久化上传；中断状态机接入）
5. `client/uni_modules/` 录音插件（UTS 录音封装，若存在；与 Agent K 的插件文件不重叠——K 管 yishu-photo-watch 与新建后台插件）
6. `research/poc/.../RecordingService.kt`（按 wav 16k 契约重做，供 UTS 集成参考）
7. `backend/tests/test_asr.py`、`test_notify.py`、新建 `test_emotion_consume.py`

## 绝不碰（只读）
dashscope.py、pipeline.py（经 pipeline_ext）、models.py/migrations/（events.emotion 列已存在）、`client/uni_modules/yishu-photo-watch/`（Agent K 域）、feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单（编号对齐 _B5a_B5d修正后待办）
1. **J-1 音频事件 3 类**：SenseVoice 输出中读取笑声/静音/键盘环境音（EMO 标签或专用输出），消费：笑声→情绪加分、静音→提示空段、键盘/环境音→"疑似非口述"标记。
2. **J-2 噪音降权**：SNR 检测（轻量），噪音大时声学情绪权重降为与语义持平（登记到情绪合并逻辑）。
3. **J-3 段级情绪合并对齐**：主导=时长最长段 + 峰值保留标记（现 max confidence）；输出结构带 dominant/peak。
4. **J-4 客户端长录音入口**：voice.ts 放开上限 + 音频上传端点（复用分片或新端点，登记 API 需求）。
5. **J-5 events.emotion 联动**：pipeline_ext/emotion.py——语音内容情绪写入其所属事件 events.emotion（主导+峰值）。
6. **J-6 情绪关怀分层触发**：notify.py 实现 SAD 未说明原因→关怀追问 / SAD 已说明→回应内容 / ANGRY→陪伴出口 / 深夜轻量 / 连续多日频次递减；<0.7 不触发；文案库占位（产品部提供后替换）；voice_done 消息接线；22:00 复盘调度登记。
7. **J-7 录音中断状态机**：RECORDING→INTERRUPTED→恢复（来电/闹钟 shouldResume）/停留暂停/30min 自动结束+分段保存；MediaRecorder.resume() 或降级分段+云端拼接；UTS 插件集成；POC-02 文档修正。
8. **J-8 FunASR 情绪验证**：登记验证项（主通道未来返回情绪 → auto 跳过本地；现状已预留开关）。

## Dependencies
- PR #1 代码（已 merge，最新 develop）
- 产品部关怀文案库（占位先行）
- 真机 nova 11（录音中断/长录音验证）

## DoD
1. 后端测试全过（情绪合并/触发逻辑 mock 可测）；客户端 HBuilderX 编译通过 + 模拟器冒烟。
2. 更新 .cowork-temp/audit_B5a_B5d_voice.md 状态列（按修正后待办）。
3. 完成消息：文件清单 + 测试 + API 需求（上传端点/调度）+ 真机状态。

## Integration
分支 `wave4-agentJ`；与 K/L 并行（J 管录音插件/voice.ts/notify；K 管 yishu-photo-watch+新后台插件；L 管 auth/wechat）；merge 后全量测试 + 契约更新。
