# 忆述光华 · 并行开发总纲（Harness v2）——新窗口 Agent 必读

> 生成：2026-08-26（Wave 0）｜维护者：集成 Agent（主窗口）
> 配套：`docs/parallel-dev/` 下各波次任务卡（02-12）+ 集成规则（13）+ B5a/B5d 修正后待办（_B5a_B5d修正后待办.md）
> 进度：Wave 1（A/C）✅、Wave 2（D/E）✅ 已集成（2026-08-26，全量 341 passed）；Wave 2 F 开发中；Wave 3（G/H/I）可启动

---

## 1. 仓库现状（2026-08-26 快照）

1. **基线分支：develop**（main 已停更，勿用）。本地与远端 origin/develop 同步。
2. **PR #1 已合并**：`codex/asr-pipeline-hardening` → develop（Wave 0 完成，commit 见 git log）。ASR 双通道主体已完成：多格式转写（M4A/MP3/AAC/WAV）、本地 SenseVoice 情绪（ONNX + 置信度门控 + `should_enhance_with_local_emotion` 开关）、VAD 分段、数字静音→no_speech、pipeline 语音状态机。**B5a 相关待办以《_B5a_B5d修正后待办.md》为准，勿再按旧 audit 报告开发。**
3. **MVP 范围修正（用户拍板）**：**桌面端（F9/B5e 全部、Zvec、真分片、Windows 定时、删除告知文案）已移出 MVP**，归"内测后/二期"。并行开发只覆盖 P0/P1 的 Android 端 + 云端 + 微信。
4. **Wave 0 已完成的代码准备**（本波次 Agent 直接使用，勿重做）：
   - `backend/sql/schema.sql` 补齐（events.client_event_id、geo_cache.province、upload_tasks/upload_chunks、profile_sensitive 5 级结构）
   - 新迁移 `backend/migrations/versions/b0b1c2d3e4f5_add_wave0_parallel_dev_tables.py`（upload_tasks/upload_chunks 补录 + profile_sensitive/sensitive_words 重建 + profile_annotation_pool 新增）
   - `models.py` 新增 ORM：ProfileSensitive（5 级处置）、SensitiveWord、ProfileAnnotationPool
   - **`backend/app/services/pipeline_ext/` 钩子包**（payload/sensitive/profile/emotion 四模块，每域 Agent 独占一个文件）
   - **`backend/app/services/llm_ops/` 聚合包**（base.py 转发 dashscope，rerank/event_merge/annotate/guard 每域一个文件）
   - `pipeline.py` 已插入 4 个钩子调用点（extend_payload / mark_sensitive_on_ingest / annotate_on_ingest / consume_emotion）
5. **环境依赖（2026-08-26 核实）**：本地依赖 **Docker Desktop** 提供 `yishu-redis`(6379) / `yishu-qdrant`(6333-6334) 容器（未启动时 test_queue/test_pipeline 等红；test_agent 已做端口自检 deselect）；PG 5432（yishu 库，迁移已到 head）；本地 .env 的 STORAGE_BACKEND=fs 会被 test_agent 覆盖为 fake。**判定测试失败前先区分环境 vs 代码**：看错误是连接拒绝（WinError 10061 / redis.exceptions）还是断言逻辑。
6. **外部 API Key（2026-08-26 新增）**：项目所需全部外部 key 的变量名/用途/获取途径/状态见 **[docs/项目API密钥清单与获取.md](../项目API密钥清单与获取.md)**（权威清单，开工前自查；含别名、MOCK 开关、常见问题）。

## 2. 并行开发模式（多窗口互不干扰）

1. **物理隔离**：每个 Agent 一个 git worktree：`git worktree add D:\GuangH-App\.wt\waveN-agentX -b waveN-agentX develop`（`.wt/` 不入库）。分支从最新 develop 切出。
2. **逻辑隔离（铁律）**：只改自己任务卡 Scope 列出的文件。**共享文件零并行写**：
   - `pipeline.py`、`dashscope.py`、`models.py`、`migrations/`、`feature_list.json`、`progress.md`、`session-handoff.md`、`AGENTS.md`、`docs/parallel-dev/`、OpenAPI 契约 —— 全部**只读**（除集成 Agent）。
   - 要挂管线 → 实现 `pipeline_ext/<你的域>.py`（已建 stub，填 TODO 即可）。
   - 要调 LLM → 经 `llm_ops/base.py`（chat_text/moderate/rewrite_query/route_query）或你自己的 `llm_ops/<域>.py`，禁止直接 import dashscope 内部。
   - 要新表/新列 → 不许动 models/迁移；把需求写进任务卡备注，集成 Agent merge 时统一评估。
3. **跨波次文件**：同波次内文件域零重叠；跨波次共享文件（如 rag.py Wave 1 Agent A 改、Wave 2 Agent F 也可能动）以"merge 后基于最新 develop 再开发"处理，任务卡已注明。
4. **提交规范**：小步提交（每完成一个 TODO 项一次 commit），commit message 前缀 `feat(waveN-agentX):`；**禁止** `git push` 到 origin（merge 由集成 Agent 统一做，防远端分支混乱）。

## 3. 波次总览

| 波次 | Agent | 域 | 关键项数 | 依赖 | 状态 |
|---|---|---|---|---|---|
| Wave 1 | A（B2 搜索） | rag/vector_store/payload 钩子/评测 | 8 | llm_ops（已备） | ✅ 已集成 |
| Wave 1 | C（B5b 护栏） | echo/sensitive/guard 钩子/pipeline_ext.sensitive | 6 | Wave 0 表（已建） | ✅ 已集成 |
| Wave 2 | D（B3 云侧） | event_aggregation/events/llm_ops.event_merge | 8 | llm_ops | ✅ 已集成 |
| Wave 2 | E（B3 端侧+UI） | client/utils/agg、index.uvue、反向入口新 API | 7 | D 的 API 契约 | ✅ 已集成 |
| Wave 2 | F（M1 补遗） | llm_ops.rerank/评测集/护栏托管/测量脚本 | 6 | DASHSCOPE key | ✅ 已集成 |
| Wave 3 | G（B4 后端） | storage/upload/sync/wechat 媒体/清理 job | 5 | COS key（代码先行） | ✅ 已集成 |
| Wave 3 | H（B4 客户端） | sync_client 接线/流量约束/定时/状态 UI | 6 | G 的 API（已就绪） | ✅ 已集成 |
| Wave 3 | I（B1 画像） | interview/标注钩子/枚举集接线 | 6 | llm_ops.annotate | ✅ 已集成 |
| Wave 4 | J（B5a 客户端） | voice.ts 长录音/关怀触发/中断状态机/emotion 钩子 | 5 | PR#1 已就绪 |
| Wave 4 | K（B5d Android） | 前台服务/WorkManager/attribution 插件 | 3 | — |
| Wave 4 | L（M3 微信） | code2session/阿里云内容安全 | 2 | 微信密钥（代码先行） |

## 4. 新 Agent 启动步骤（每次开工前必做）

1. 读本文件（总纲）→ 读自己的任务卡（docs/parallel-dev/0X_...md）→ 读《13_集成规则》与《_B5a_B5d修正后待办》（涉语音域时）。
2. `cd D:\GuangH-App && git fetch origin && git pull origin develop` 确认主干最新。
3. `git worktree add D:\GuangH-App\.wt\waveN-agentX -b waveN-agentX develop`（若已存在则 `git worktree prune` 后重建）。
4. 在 worktree 内开发；每完成一个 TODO 项：跑该域测试 → commit。
5. **提交门禁（快/全量拆分，2026-08-26）**：commit 时 pre-commit 自动跑**快速门禁**（秒级，只查本次提交文件）；**完成声明前必须跑全量门禁 `python scripts/review_agent.py --full`**（仓库级 + 全量测试），集成 Agent 与 CI 也以此验收。
6. 全部完成：更新自己域的 audit 记录（docs/parallel-dev/ 或 .cowork-temp/）→ **显式呼叫集成 Agent**（在完成消息里列出：改了哪些文件、新增了哪些端点/表需求、哪些测试通过/失败、哪些待 key 验证）。**禁止静默完成。**

## 5. 完成定义（DoD，每 Agent 通用）

1. 域内 pytest 全过（环境缺失项标注"环境依赖，CI 验证"）；ruff 干净。
2. 涉及 API：同步更新 OpenAPI 契约需求（写到完成消息，由集成 Agent 重导出）。
3. 更新自己域的 audit/待办状态（把完成项从清单划掉）。
4. 显式呼叫集成 Agent（消息含文件清单 + 测试结果 + 表需求 + 待 key 项）。
5. 不 push、不改共享文件、不碰别人的域。
