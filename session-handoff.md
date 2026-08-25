# Session Handoff — 忆述光华

> 交接文档（2026-08-25 更新）。目标：**任何 Agent/成员接手后 15 分钟内能上手**。
> 完整状态见 [progress.md](progress.md)（状态卡 + 完成/待办）；本文件优先记录当前工作区与下一步。

## 当前状态（2026-08-25，ASR + 声学情绪开发会话）

- **仓库副本**：`D:\360MoveData\Users\Eason\Desktop\formal development\YSGH-APP-validation`
- **分支**：`codex/asr-pipeline-hardening`；基于团队提交 `3869111b51ec9729e0a248298dbcafa23f537bfb`
- **Git 状态**：ASR 改动已完成音频范围门禁与提交准备；以 `git log/status` 为准，**尚未 push**
- **用户范围**：用户只负责音频处理；不接手机 App，不应擅自修搜索/分类等无关模块
- **用户决定**：个人 API Key 只允许临时验证；任何 push 必须等用户最终明确确认
- **密钥状态**：个人 Key 仅注入一次性进程环境完成真实 FunASR 验证，未写入工作区/持久环境；不要从聊天或日志复制进代码
- **相关验证**：ASR/语音入库/内容接口共 `40 passed`；ruff、py_compile 通过；真实 M4A 的云端转写与本地情绪推理均已验证
- **仓库总门禁**：非 ASR 基线仍有 `15 failed / 1 error`，详见下文；不要把它误解为 ASR 未通过，也不要绕过后声称全绿
- **测试基础设施**：隔离容器 `ysgh-validation-postgres` / `ysgh-validation-redis` / `ysgh-validation-qdrant`
- **develop 基线**：已包含团队后续 17 个提交；此前记录为 pytest 215 passed（14 deselected）+ research 验证入口修复，本轮音频变更需在该基线上重新验证

## 本轮 ASR 已开发内容

### 1. 主通道与格式

- `backend/app/services/external/asr.py` 主通道升级为 Fun-ASR Flash：`fun-asr-flash-2026-06-15`
- 使用 Base64 Data URI 请求，支持 AAC/AMR/FLAC/M4A/MP3/OGG/OPUS/WAV/WebM/WMA
- 云端转写成功后，本地 CPU `iic/SenseVoiceSmall-onnx` 独立执行声学情绪增强；云端失败时也可作为本地转写降级
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
结果：40 passed，1 个 Starlette/httpx 弃用 warning

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

1. 先向用户复述：情绪检测已完成，代码尚未 push；展示上述 `40 passed` 与真实 M4A 证据
2. 下一项仅在音频范围内推进：补“录音中断/恢复”状态机真机联调，或等待用户指定优先项
3. 全仓门禁的搜索/分类/数据库基线不属于当前用户范围；继续使用上文 ASR 范围门禁，不要声称全仓全绿
4. 本地 commit 后仍不要 push；必须再次获得用户明确的最终确认才可推到团队仓库
5. 多样本验收不是本轮快速跑通条件；后续正式校准再收集 20-50 段标注录音评估 WER 与情绪准确率
