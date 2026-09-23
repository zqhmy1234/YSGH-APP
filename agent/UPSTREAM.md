# Agent 交付包接入说明（UPSTREAM）

## 一、来源与指纹

| 项 | 值 |
|---|---|
| 交付方 | 开发部其他同事（Coze Vibe Coding 导出项目，`pyproject` name=`vibe-coding`） |
| 原始包 | `project_20260923_200328.tar.gz`（0.3 MB，2026-09-23 20:03） |
| **SHA256** | `82314B8D7A56F2121BB2960500F65A4B9642E9635566E4AEAAF27B840967E5D3` |
| 入库方式 | **全量**搬入 `agent/`（50 文件 / 778.8 KB，未删减；含 `assets/` 截图、`uv.lock`、`.coze`） |
| 入库时间 | 2026-09-23 |

**未改造前，本目录内容 = 上游原样**（便于逐批对照）。每批改造在此追加差异记录。

## 二、它是什么（读码结论）

- **形态**：Coze 托管项目。`.coze` 的 entrypoint=`src/main.py`；`scripts/http_run.sh` 以 `uvicorn` 起 **5000** 端口。
- **对话主链**：`src/agents/agent.py` 用 LangGraph `create_agent(model, system_prompt, tools=13, middleware, checkpointer, state_schema=AgentState)`；**`src/graphs/` 是空的**（只有 0 字节 `__init__.py`），无自定义图/节点。
- **13 个工具**：`tools/memory_tools.py`（记忆/收藏 CRUD 与检索）· `tools/tag_rules.py`（**标签枚举与同义词的唯一真源**）· `tools/url_fetch_tools.py`（链接抓取/导出/关联）· `tools/voice_tools.py`（语音转写）· `tools/wechat_push_tools.py`（**空占位**，8 行）。
- **数据**：Supabase PostgREST 读写三表——`memories` / `knowledge_collections` / `memory_categories`（ORM 声明在 `src/storage/database/shared/model.py`，**无 alembic**）。
- **双入口**：App/HTTP（`src/main.py`）+ **企微机器人**（`src/main_wechat.py`：`WSClient` 长连接，独立进程）+ **22:00 定时复盘**（`services/daily_review_service.py`，30s 轮询 `wechat_users.review_enabled`）。
- **鉴权：完全没有**。数据归属只认可伪造的 `X-User-Id` header / `user_id` query（`src/web/api_routes.py:30-39`），兜底 `COZE_USER_ID`。
- **契约**：路由为 `/api/*` + 裸 `/run`／`/stream_run`／`/v1/chat/completions`，**无 `/api/v1` 前缀、无 Pydantic 模型、无 message id**；仅 `/stream_run` 是 SSE（`main.py:509`）。

## 三、接入决策（2026-09-23 · 用户拍板）

| 维度 | 决策 |
|---|---|
| 形态 | **独立进程 + 我们后端反代**（我们侧做 JWT → **可信 user_id 注入**，不透传客户端 header）；且**全量挪到本地、彻底去掉 Coze 运行时依赖** |
| 数据归属 | **落我们自己的 Postgres**：alembic 建三表；Supabase PostgREST 调用全部改写为 SQLAlchemy repository |
| 首接入口 | **App 客户端 AI 页**：实现 `POST /api/v1/chat/messages` + `GET /api/v1/chat/replies/:id`（契约已在 `client/components/TabAi/TabAi.uvue` 头注释与 `_diff_ledger.md:500` 登记，`reply.kind ∈ bubble\|plain+chips\|cards\|confirm\|typing`） |
| 模型 | **阿里云百炼**（OpenAI 兼容端点 + `DASHSCOPE_API_KEY`），复用 `backend/app/core/config.py:54-59` 既有凭证口径（`dashscope_api_key` / `dashscope_workspace_id` / `dashscope_base_url`） |
| 企微入口 | **本轮不动**（独立进程、独立凭证；App 入口打通后单批处理） |

## 四、去 Coze 改造映射表（每批施工依据，完成一项勾一项）

| # | Coze 依赖 | 出现位置（证据） | 替换为 | 状态 |
|---|---|---|---|---|
| 1 | `coze_workload_identity`（**所有 env + S3 token 都从它拉**） | `scripts/load_env.py:18-21`（根本不读 `.env`）、`storage/database/db.py:22-33`、`storage/database/supabase_client.py`、`storage/s3/s3_storage.py:63-82` | `python-dotenv` 读 `.env`；token 概念随 Supabase/S3 代理一并消失 | ⏳ |
| 2 | `coze_coding_dev_sdk.database.Base` | `storage/database/shared/model.py:1` | 自有 `declarative_base`（新增 `storage/database/base.py`） | ⏳ |
| 3 | Supabase PostgREST（约 40 处 `client.table(...)`） | `tools/memory_tools.py`、`tools/url_fetch_tools.py:123/155/263/280`、`web/api_routes.py:58-84/116/173`、`services/wechat_service.py:90-102`、`services/daily_review_service.py:44-53` | SQLAlchemy repository（同三表，落我们 Postgres） | ⏳ |
| 4 | Coze 模型网关（`COZE_INTEGRATION_MODEL_BASE_URL` + `coze` identity key；模型 `glm-4-7-251222`） | `agents/agent.py:74-90`；`config/agent_llm_config.json:12` | 百炼 OpenAI 兼容端点 + `DASHSCOPE_API_KEY` + 模型名可配 | ⏳ |
| 5 | `coze_coding_dev_sdk.ASRClient` | `tools/voice_tools.py:11,94`（且前置依赖**系统 ffmpeg**，见 `:34-45`） | 复用我们后端既有百炼 ASR（`backend/app/services/external/asr/`）；App 路径下语音本就走我们后端，此项可能整体不需要 | ⏳ |
| 6 | `coze_coding_dev_sdk.FetchClient`（网页抽取） | `tools/url_fetch_tools.py:35` | 自建 httpx + 正文抽取（**后端目前无此能力**，需新增；`grep url_fetch/readability/html2text` 零命中） | ⏳ |
| 7 | Coze S3 代理预签名（`${endpoint}/sign-url` + `x-storage-token`，region 硬编码 `cn-beijing`） | `storage/s3/s3_storage.py:233-289`、`tools/url_fetch_tools.py:204` | 复用 `backend/app/services/external/storage.py`（COS 原生 `presigned_get_object`） | ⏳ |
| 8 | `coze_coding_utils`（context/request_context/stream_runner/async_tasks/log/错误分类） | `src/main.py:15-53` 等 | stdlib logging + **显式 user_id 注入**（`request_context` → 函数参数，消除隐式全局态） | ⏳ |
| 9 | `cozeloop`（观测上报） | `src/main.py:10` + 4 处 `cozeloop.flush()`（`:170/445/569/587`） | 移除（或接我们自己的日志口径） | ⏳ |
| 10 | `wecom_aibot_sdk` | `src/main_wechat.py`、`services/wechat_service.py:67` | **本轮保留**（企微独立批次处理） | ➖ |
| 11 | `langsmith`（LangChain 云追踪） | 依赖项 | 移除或置可选 | ⏳ |

**依赖瘦身计划（`pyproject.toml`）**：移出 `coze-coding-utils`、`coze-workload-identity`、`coze-coding-dev-sdk`、`cozeloop`、`supabase`、`langsmith`，以及 **Linux 桌面库** `dbus-python` / `PyGObject` / `pycairo`（Windows 上装不上，是本包在 Windows 侧的第一号环境雷）。
**待核实用途后再定去留**：`opencv-python`、`pandas`、文件解析族（`pypdf` / `docx2python` / `openpyxl` / `python-pptx`）。
**保留**：`langchain*`/`langgraph*`（主链）、`fastapi`/`uvicorn`/`pydantic`（服务）、`SQLAlchemy`/`psycopg*`/`alembic`（数据层）。

## 五、已知缺陷与坑（接入时必须一并处理，不是"以后再修"）

1. **无鉴权 + 用户 id 可伪造**（`api_routes.py:30-39`）→ 必须由我们后端反代注入可信 `user_id`，且 agent 服务**不得直接暴露公网**。
2. **HTTP 路径的多轮上下文不连续**：`run_agent` 用 `thread_id=user_id`（`agent.py:145`），而 `/run`、`/async_run` 用 `thread_id=run_id`（`main.py:119/316`）→ 反代时必须固定透传稳定的 `thread_id`/`session_id`，否则"每次对话都是新的"。
3. **checkpoint 静默降级**：DB 不可用即退化为进程内 `MemorySaver` 且**不报错**（`memory_saver.py:84-88`），表现为"对话突然失忆"→ 需显式探测与告警。
4. **配置三处不一致**：`.env.example` 写 `SUPABASE_URL/SUPABASE_API_KEY`，代码读 `COZE_SUPABASE_*`；`.env.example` **缺 `WECHAT_BOT_SECRET`**（`main_wechat.py:29-30` 却强制要求）；`agent_llm_config.json` 的 `thinking_type` 与 `agent.py:86` 读的 `thinking` 键名不匹配 → **thinking 实际恒 disabled**。
5. **`/run` 的取消表是进程内 dict**（`main.py:62`）+ `workers=1`（`:659`）→ 横向扩容前必须处理。
6. **无 CORS / 无限流 / 无请求体上限**（`main.py:287` 后无 middleware）→ 若被反代，网关侧补齐。
7. **评测标准是文档、无脚本**：`docs/evaluation_standard.md` 有 8 维度加权评分与阈值（≥4.5 优秀），但仓库内**无配套自动评测脚本与测试集**（全仓仅 `tests/test_user_isolation.py` 一个测试）。
