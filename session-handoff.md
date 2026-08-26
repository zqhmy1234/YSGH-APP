# Session Handoff — 忆述光华

> 交接文档（2026-08-25 更新）。目标：**任何 Agent/成员接手后 15 分钟内能上手**。
> 完整状态见 [progress.md](progress.md)（状态卡 + 完成/待办）；本文件优先记录当前工作区与下一步。

## 当前状态（2026-08-25，ASR + 声学情绪开发会话）

- **仓库副本**：`D:\360MoveData\Users\Eason\Desktop\formal development\YSGH-APP-validation`
- **分支**：`codex/asr-pipeline-hardening`；本地已 rebase 到 `origin/develop`（`ce62b33`）
- **Git 状态**：PR 评论的 5 项修复已纳入 `codex/asr-pipeline-hardening`，现有 PR 以 `develop` 为 base；具体提交以 `git log` 和 PR 页面为准
- **用户范围**：用户只负责音频处理；不接手机 App，不应擅自修搜索/分类等无关模块
- **用户决定**：个人 API Key 只允许临时验证；任何 push 必须等用户最终明确确认
- **密钥状态**：个人 Key 仅注入一次性进程环境完成真实 FunASR 验证，未写入工作区/持久环境；不要从聊天或日志复制进代码
- **相关验证**：ASR/语音入库/内容接口共 `49 passed`；ruff、py_compile 通过；真实 M4A 的云端转写与本地情绪推理均已验证
- **仓库总门禁**：非 ASR 基线仍有 `15 failed / 1 error`，详见下文；不要把它误解为 ASR 未通过，也不要绕过后声称全绿
- **测试基础设施**：隔离容器 `ysgh-validation-postgres` / `ysgh-validation-redis` / `ysgh-validation-qdrant`
- **develop 基线**：团队后续 17 个提交已纳入本地分支，本轮 49 项音频范围测试已在该基线上重跑通过

## PR 评论 5 项修复

1. 分支已 rebase 到 `develop`，现有 PR 的 base 已改为 `develop`。
2. `numpy>=1.26` 已作为直接依赖写入 `backend/requirements.txt`。
3. 新增 `backend/scripts/prepare_sensevoice.py`；生产环境必须预置模型并配置 `SENSEVOICE_MODEL_DIR`，首次请求不再下载权重。
4. workspace 专属地址改由 `DASHSCOPE_REGION` 拼接，仍支持 `DASHSCOPE_BASE_URL` 完整覆盖。
5. 云端转写先完成，本地情绪改由低优先级 RQ 任务异步增强；`ASR_LOCAL_EMOTION_MODE=auto` 时，主通道已有情绪便跳过本地模型。

## 本轮 ASR 已开发内容

### 1. 主通道与格式

- `backend/app/services/external/asr.py` 主通道升级为 Fun-ASR Flash：`fun-asr-flash-2026-06-15`
- 使用 Base64 Data URI 请求，支持 AAC/AMR/FLAC/M4A/MP3/OGG/OPUS/WAV/WebM/WMA
- 云端转写成功后，本地 CPU `iic/SenseVoiceSmall-onnx` 由低优先级任务异步执行声学情绪增强；云端失败时仍可作为本地转写降级
- M4A/MP3/AAC/WAV 等常见格式由内置 FFmpeg 统一解码为 16kHz 单声道后进入情绪模型，不再只有 WAV
- API 上传仍限制 8MB；内部对象存储的长 WAV 可进入 VAD，单段最长 4 分钟
- 超过 8MB 的压缩音频明确失败，提示切分或转 WAV，不会误走本地 WAV 分段

### 2. 状态语义（已修复“假完成”）

- 有真实文本：`succeeded`
- 数字静音、长录音 VAD 无语音或供应商明确空文本：`no_speech`
- 临时网络/限流/供应商异常：`failed_retryable`
- 缺 Key、无音频、格式错误等确定性问题：`failed_final`
- 语音主步骤失败会写 `content.status=failed`，并保存到 `extra.audio_processing`；不会再出现数据库无文本但状态为 `done`
- `no_speech` 是正常空结果：内容为 `done`，但带明确 `audio_processing.outcome=no_speech`，且不进入事件聚合
- 未分类异常也会落库为 `ASR_PIPELINE_ERROR / failed_retryable`，避免长期卡在 `processing`

### 3. 审计与安全

- 保存实际通道、模型、供应商 request id、音频格式、源文件 SHA-256、时长、情绪、情绪置信度/来源/模型/是否可触发、segments、usage、降级错误
- 情绪置信度来自 SenseVoice 的情绪 logits，和 ASR 文本置信度分开；低于 0.7 只记录、不标记为可触发
- 生产模式在入口即拒绝全局或显式 mock；开发/测试 mock 与真实结果结构保持一致
- 重试只针对可重试错误，采用指数退避；错误返回不包含 API Key
- 空白语音跳过文本护栏并返回 `guardrail.passed=true / reason=no-speech`

### 4. 本轮修改文件

- 核心：`backend/app/services/external/asr.py`、`backend/app/services/pipeline.py`
- 依赖/模型：`backend/requirements.txt`、`backend/models/README.md`
- API/契约：`backend/app/api/asr.py`、`backend/app/schemas/asr.py`、`backend/app/api/contents.py`、`backend/app/schemas/content.py`
- 测试：`backend/tests/test_asr.py`、`backend/tests/test_pipeline.py`
- 进度：`feature_list.json`、`progress.md`、`docs/lessons.md`
- 本交接：`session-handoff.md`

### 5. 已跑验证

```text
pytest backend/tests/test_asr.py backend/tests/test_pipeline.py::TestVoicePipeline backend/tests/test_contents.py
结果：49 passed，1 个 Starlette/httpx 弃用 warning

ruff（全部本轮 Python 文件）：通过
py_compile（全部本轮 Python 文件）：通过
init.ps1：通过
```

真实验证使用现有 5 秒 M4A：FunASR Flash 云端返回 `succeeded`；同一文件由本地 SenseVoiceSmall 判定“平静”，情绪置信度约 0.8741。单元测试覆盖云端转写与本地情绪合并、情绪失败不抹掉文本、生产禁用 mock；多录音 WER/情绪准确率校准仍待后续数据。

## 总门禁现状（不要扩大用户范围）

- `scripts/review_agent.py` 的全仓报告中，ASR 定向测试已绿，但无关基线仍为 `15 failed / 1 error`
- 主要原因：干净副本缺 BGE-M3/SetFit 数 GB 本地模型、PG 缺 pgvector、research 验证仍导入旧路径、同步测试清理残留
- `review_agent.py` 还会把包含其他 `No module named` 的 pytest 失败误报为“pytest 未安装并 skip”；不要利用该误判提交
- 用户已明确自己只负责音频处理。除非用户另行授权，不要下载数 GB 模型，也不要修改无关搜索/分类模块
- 当前不能声称仓库总门禁全绿；是否采用 ASR 范围门禁提交，应由团队维护者确认仓库规则

## 新成员启动步骤（15 分钟上手）

1. **读**：`AGENTS.md`（操作指令）→ `progress.md`（状态+完成/待办）→ `docs/lessons.md`（环境陷阱+教训）→ 本文件
2. **环境**：`.\init.ps1` 校验基线；确认 PG/Redis/Qdrant 运行；模型在 `backend/models/`（清单见该目录 README.md）
3. **密钥**：按 `skills/infisical-secrets/SKILL.md`（`infisical run --env=dev -- <cmd>` 注入；本地 .env 有 MOCK_EXTERNAL_AI=true 会走 mock，真实调用需显式 `$env:MOCK_EXTERNAL_AI='false'`）
4. **测试**：`cd backend && python -m pytest -q`（全量）；`pytest -m rag`（RAG 集成）
5. **提交**：先确认本文件中的未提交 ASR 差异；不要 push。总门禁基线未恢复前，不要绕过 pre-commit

## 团队 GitHub 上传注意事项

- **禁止上传**：`.env`（含 MOCK_EXTERNAL_AI 等）、任何 API key、`.cowork-temp/`、个人隐私文件（见 .gitignore）
- **密钥管理**：全部走 Infisical（dev/prod 各 10 条），代码零硬编码
- **文档位置**（已归位）：
  - harness：根目录 AGENTS.md / progress.md / session-handoff.md / feature_list.json / init.ps1 / init.sh
  - 教训/陷阱：`docs/lessons.md`（唯一权威）
  - 交付文档：`忆述光华_交付文档/`（定稿参考，修改需用户同意）
  - 运行文档：`docs/`（拿key后推进计划 / B2对照 / 生产兜底审计等）
- **模型资产**：`backend/models/README.md`（防重复下载；大模型文件不入库）

## 已知阻塞 / 风险

- 缺外部凭证：微信 appid/secret、企微回调、Sentry DSN、高德 key（对应 F6/Sentry/逆地理阻塞）
- 合规三申请未启动（企微认证/ICP/软著，M0 硬依赖，5-6 周串行）
- UTS POC 需 Android 原生 Kotlin 人力（全局 Gate）
- 客户端（APP/Windows）整体未启动；后端 API 约 70% 就绪
- RAG 上线评测集（50 条真实查询）等团队数据

## 下一步（仅 ASR 范围）

1. 等待团队对现有 PR 的下一轮审查；如有新的音频范围评论，再做定向修复
2. 生产发布时运行 SenseVoice 预置脚本并配置 `SENSEVOICE_MODEL_DIR`
3. 全仓门禁的搜索/分类/数据库基线不属于当前用户范围；继续使用上文 ASR 范围门禁，不要声称全仓全绿
4. 后续修改仍先跑音频范围门禁，再更新现有 PR
5. 多样本验收不是本轮快速跑通条件；后续正式校准再收集 20-50 段标注录音评估 WER 与情绪准确率

## Wave 1 集成完成（2026-08-26 03:20）——下一步 Wave 2

- develop HEAD：23b55f4（merge A+C）+ 待提交集成 commit
- Wave 1 完成：B2 搜索域（FIX-1 content_type 归一/payload place·tags/事件归因/PG 兜底/caption 缓存/P95 2013ms）+ B5b 护栏域（FIX-4 画像敏感接线/事件级敏感分类器/违规回流/检测器抽象）+ 集成接线（router 注册/photo payload 后补/搜索规则级敏感过滤）
- 基线：312 passed + api_smoke 6/6 + review_agent 全绿
- 下一步：Wave 2（Agent D B3 云侧 / Agent E B3 端侧+UI / Agent F M1 补遗），任务卡 docs/parallel-dev/04/05/06；共享文件规则不变（pipeline.py 钩子/llm_ops 只读）
- 注意：worktree 开发需补本地 gitignore 资产（.env/models/测试照片，见 lessons.md 两 Agent 踩坑记录）

## Wave 2 集成完成（2026-08-26 06:30）——下一步 Wave 3

- develop HEAD：fe1b376（Wave 2 集成接线）+ ae801a7（merge E）+ 7e8c142（merge D）
- Wave 2 完成：Agent D（B3 云侧：L2 地点域连续+LLM 归并裁决（真实 qwen 验证）/L3 7 天窗+生命周期/封面选择/GPS 漂移完善/confirmed 保护/增量先匹配后分裂/OCR 内容维）+ Agent E（B3 端侧+UI：30min 保守开关/预处理去重/L2 待确认区/封面+反向入口 GET /api/v1/contents/{id}/events/30s 验收埋点/AGG 双跑 14 用例）
- 集成接线：云侧 AGG_CONFIG conservative_mode 对齐端侧（l0_eps_t_sec()）；修复 D 合并代码 B905 lint；lessons 冲突保留两边
- 基线：341 passed（fullgate-wave2.log 为准）+ review_agent --full 全绿
- 下一步：Wave 3（Agent G B4 后端 / Agent H B4 客户端 / Agent I B1 画像），任务卡 docs/parallel-dev/07/08/09；Agent F（M1 补遗）仍在开发，merge 后接入
- 待办登记：Content.extra quality_score/face_count 无写入方（腾讯 CI 人脸标签未接线）；托管护栏 guard_managed 待 F

## Wave 3 集成完成（2026-08-26 14:30）——下一步 Wave 4

- develop HEAD：f85a393（集成接线）+ 1f958fe（merge I）+ 0899ba6（merge H）+ feb3a09（merge G）+ 690596b（merge F）
- Wave 3 完成：F（M1 补遗：LLM 精排第二层/托管护栏/50 条真值评测集/纠错测量）+ G（B4 后端：缩略图管线/upload_mode 流量约束/微信媒体上云/30 天清理/COS 验证文档）+ H（B4 客户端：sync_client 字段级同步/流量约束/退避/批量暂停/UploadStatusBanner）+ I（B1 画像：枚举集收尾入 git/annotator 标注核心/钩子接线/冷启动兴趣稀疏）
- 集成接线：thumbnails_router 注册 + index.uvue UploadStatusBanner + l2_evidence CASCADE 迁移 c7d8e9f0a1b2 + schema.sql 同步
- 顺手：lessons.py 时区修复（Asia/Shanghai）+ docs/项目API密钥清单与获取.md（全 key 清单/获取途径）
- 基线：420 passed + api_smoke + research + review_agent --full 全绿
- 下一步：Wave 4（Agent J B5a 客户端 / Agent K B5d Android / Agent L M3 微信），任务卡 docs/parallel-dev/10/11/12
- 待办：COS/微信/Sentry key 到位后实网验证（smoke_cos/微信媒体下载/托管护栏）；B/C/D 采集语料后重跑评测；H 真机补验；cleanup job 需挂调度（建议系统 cron/rq-scheduler 每日低峰 python -m app.workers.cleanup_job --older-than-days 30）

## CI 全链路修复完成（2026-08-26 19:00）——CI #21 全绿

- develop HEAD：a92212b（warm_hf 强制在线）；CI #21 Fast + Full Gate 全绿（首次双绿）
- 根因链（#8-#21，7 个根因逐一修复，均登记 docs/lessons.md）：
  1. #8 postgres 就绪竞态 → Init PG 加重试循环
  2. #9-#12 alembic 迁移链不自包含（baseline 仅 alter_column 假设表已由 schema.sql 预建）→ 回退 schema.sql 建库；**issue #2 方向修正：CI 建库不能跑 alembic**
  3. #13 步骤级 env PGPASSWORD 单密码覆盖多用户 psql → 每条命令内联各自密码
  4. #15 schema.sql 缺 profile_annotation_pool（迁移建表未同步）→ 补齐（本地临时库验证 38 表）
  5. #16 pgvector 扩展缺失 + 测试 FK 清理不完整（本地旧库 27 表/4 FK 掩盖）→ schema.sql/setup_pg.sql 加 CREATE EXTENSION + CI 镜像 pgvector/pgvector:pg16 + 测试 fixture 补子表清理
  6. #17-#18 qdrant server 1.9.7 与 client>=1.19 不兼容 → 镜像升 v1.19.0
  7. #19-#20 CI 全新缓存 BGE-M3 现场下载失败 → 新增 Warm HF models 步骤（scripts/warm_hf_models.py，强制在线）+ 失败详情写 annotation（API 匿名可读）
- 本地验证：全新库 + vector + schema.sql + review_agent --full = pytest 419 passed（唯一失败为复现库名假阳性）+ api_smoke 6/6 + research 18 全过
- 遗留：Node.js 20 弃用警告（actions 升级到 v5/v6 可消除，非阻断）
- 漂移防护：schema.sql vs 迁移链表名 diff 脚本（.cowork-temp/diff_schema_migrations.py 思路），lessons.md 已登记「三处漂移」教训


## Wave 4 集成完成（J/L，2026-08-26 20:20）——Agent K（B5d Android）未完成

- develop HEAD：deb6e24（Wave4 J/L 集成接线）+ a0fe630（merge L）+ ab6d447（merge J）；Wave 4 三 Agent 并行，J（B5a 客户端/消费域）与 L（M3 微信域）已完成并合入，K 仍在开发（分支 wave4-agentK）
- Wave 4 J（B5a 消费域）：后端 ASR 音频事件 3 类解析与消费/SNR 噪音降权/段级合并 dominant+peak（backend/app/services/external/asr.py）、events.emotion 联动钩子（pipeline_ext/emotion.py）、情绪关怀分层触发 + voice_done 接线 + 22:00 调度登记（services/notify.py）、AsrTranscribeResponse 新增 7 字段（audio_events/emotion_bonus/silence_hint/not_oral/snr_db/noise_weight/emotion_merge）、客户端长录音 30min 上限 + 分片持久化上传 + UTS 录音插件中断状态机 + 手动停止误触发 onAutoStop 修复
- Wave 4 L（M3 微信域）：code2session 真实接入（unionid 优先回退 openid，业务 errcode→401/上游→502，未配 key 保持 mock/501）+ 内容安全可插拔适配器（规则/腾讯 CI/阿里云 Green 三实现 + off，缺 key 显式失败，fail-safe 放行不丢消息）
- **集成接线（本段）**：
  1. upload/complete meta.content_type=voice → register_photo_content voice 分支：对象搬 voice/{user}/ 前缀 + 建 content_type=voice + 入队 ASR（不再落 stray photo）；duration_ms 校验
  2. /contents voice 带 cos_key 幂等（同用户同 cos_key 返回既有记录——旧客户端 saveVoiceContent 二次建内容防重）
  3. 客户端 uploadVoicePersistent 优先用 complete 返回的 content_id，旧后端回退二次建内容
  4. pipeline.py enrich_content_emotion 补 consume_emotion 调用（本地情绪增强后的真情绪触发事件层联动/关怀；幂等安全）
  5. OpenAPI 重导出（docs/openapi.json，46 路径；AsrTranscribeResponse 已含 7 新字段）
  6. test_pipeline fixture 补 Message 清理（B5a 情绪消费写 messages，完整 FK 下删 user 被拦）
- **22:00 复盘调度（AgentJ 需求 2，部署侧）**：挂系统 cron/rq-scheduler/APScheduler，每天本地 22:00 跑 `python backend/scripts/daily_review.py`（幂等，无内容自动跳过）
- **基线**：pytest **467 passed / 19 deselected**（420 基线 + J 19 + L 26 + 集成新增 3）+ review_agent --full exit 0 全绿（syntax 183 文件/lint/secrets/tests 覆盖率≥50%/research 场景全过）
- **待 key 项**：WECHAT_APPID/WECHAT_SECRET（code2session 生产启用）、企微三件套 WECHAT_CORP_ID/TOKEN/ENCODING_AES_KEY（回调）、ALIYUN_ACCESS_KEY_ID/SECRET（内容安全，上架前可选加固，当前腾讯 CI 顶替）；阿里云 Green 实现未实网验证（签名按官方文档，待 key 校准）
- **遗留**：Agent K（B5d Android 后台录音）完成后二次集成；J 真机录音中断（来电模拟）/30min 自动结束人工实测待办（已登记 audit_B5a_B5d_voice.md）；关怀文案库正式文案待产品部
