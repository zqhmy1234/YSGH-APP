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
| 1 | `coze_workload_identity`（**所有 env + S3 token 都从它拉**） | `scripts/load_env.py:18-21`（根本不读 `.env`）、`storage/database/db.py:22-33`、`storage/database/supabase_client.py`、`storage/s3/s3_storage.py:63-82` | `python-dotenv` 读 `.env`；token 概念随 Supabase/S3 代理一并消失 | 🚧 AG2 完成 `scripts/load_env.py` 重写；`db.py` / `supabase_client.py` / `s3_storage.py` 待 AG3/AG4 |
| 2 | `coze_coding_dev_sdk.database.Base` | `storage/database/shared/model.py:1` | 自有 `declarative_base`（新增 `storage/database/base.py`） | ✅ AG2 完成（`model.py` 已换源；三表 alembic 待 AG3） |
| 3 | Supabase PostgREST（约 40 处 `client.table(...)`） | `tools/memory_tools.py`、`tools/url_fetch_tools.py:123/155/263/280`、`web/api_routes.py:58-84/116/173`、`services/wechat_service.py:90-102`、`services/daily_review_service.py:44-53` | SQLAlchemy repository（同三表，落我们 Postgres） | ✅ AG3 完成（PostgREST 兼容薄层，25+ 调用点零改动；见六·AG3） |
| 4 | Coze 模型网关（`COZE_INTEGRATION_MODEL_BASE_URL` + `coze` identity key；模型 `glm-4-7-251222`） | `agents/agent.py:74-90`；`config/agent_llm_config.json:12` | 百炼 OpenAI 兼容端点 + `DASHSCOPE_API_KEY` + 模型名可配 | ✅ AG2 完成并**实测可用**（见六） |
| 5 | `coze_coding_dev_sdk.ASRClient` | `tools/voice_tools.py:11,94`（且前置依赖**系统 ffmpeg**，见 `:34-45`） | 复用我们后端既有百炼 ASR（`backend/app/services/external/asr/`）；App 路径下语音本就走我们后端，此项可能整体不需要 | ⏳ |
| 6 | `coze_coding_dev_sdk.FetchClient`（网页抽取） | `tools/url_fetch_tools.py:35` | 自建 httpx + 正文抽取（**后端目前无此能力**，需新增；`grep url_fetch/readability/html2text` 零命中） | 🚧 AG4：`platform/fetch.py` 已成（**结果契约照上游逐字段对齐** + SSRF 防护 + 体积/超时上限）；待切换 `url_fetch_tools._init_fetch_client` |
| 7 | Coze S3 代理预签名（`${endpoint}/sign-url` + `x-storage-token`，region 硬编码 `cn-beijing`） | `storage/s3/s3_storage.py:233-289`、`tools/url_fetch_tools.py:204` | 复用 `backend/app/services/external/storage.py`（COS 原生 `presigned_get_object`） | ⏳ |
| 8 | `coze_coding_utils`（context/request_context/stream_runner/async_tasks/log/错误分类） | `src/main.py:15-53` 等 | stdlib logging + **显式 user_id 注入**（`request_context` → 函数参数，消除隐式全局态） | 🚧 AG4 切片一：`platform/{context,logging_setup,errors}.py` 已成，**22 处导入已换**（含各 tool/service/web）；剩 `main.py` 的平台编排件（12 处）→ AG4b 整体替换 main.py |
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

## 六、改造差异记录（逐批追加）

### AG2 · 去 Coze 核心（2026-09-23）

**改动文件**

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/llm_provider.py` | **新增** | 模型接入**单一真源**：模型 id / base_url / 鉴权 header / 推理开关收成一处；缺 key **显式抛错**（不静默 mock）；`describe()` 可安全打印（**不含 key**） |
| `.env.example` | **新增** | 去 Coze 后的环境变量**唯一真源**（上游那份列 `SUPABASE_*` 而代码读 `COZE_*`，见五-4） |
| `scripts/load_env.py` | **重写** | `coze_workload_identity` → `python-dotenv` 读 `agent/.env`；缺 `.env` 时给可执行的补救提示（保留 `eval $(...)` 兼容） |
| `src/storage/database/base.py` | **新增** | 自有 `DeclarativeBase`（SQLAlchemy 2.0 风格），替代 `coze_coding_dev_sdk.database.Base` |
| `src/storage/database/shared/model.py` | 改 1 行 | Base 换源为自有基类；补注"三表由 backend 的 alembic 统一创建" |
| `src/agents/agent.py` | 改 `build_agent` + import 块 | 模型改 `build_chat_model()`；**修正 thinking 键名 bug**（`thinking.type` → 官方 `enable_thinking`）；配置路径改相对本文件（不再依赖 `COZE_WORKSPACE_PATH`）；移除两处已成死引用的上游导入（`ChatOpenAI`、`coze_coding_utils...default_headers`） |
| `config/agent_llm_config.json` | 改 2 键 | `model`: `glm-4-7-251222` → **`qwen3.8-flash`**；`thinking_type` → **`enable_thinking`**（与真实参数名对齐） |

**对应映射表**：#1（部分）· #2（✅）· #4（✅）

**实证（非纸面推断）**：真实调用一次 `qwen3.8-flash`——
`model=qwen3.8-flash` / `finish_reason=stop` / 回复 `'收到'` / **`reasoning_content` 有 95 字**（证明 `enable_thinking` 真生效）/ 用量 prompt=66 completion=55。
⇒ 四项外部事实（模型 ID、workspace 专属 base_url、`X-DashScope-WorkSpace` header、`enable_thinking`）**全部实测可用**；凭证取 `backend/.env`（空间级 key，`sk-ws-` 前缀，len=115）。

**验证状态**：`py_compile` 5 文件通过；新增 3 文件 `ruff` 干净（⚠️ 因 `agent/` 在 lint 豁免名单内，须手工 `ruff check … --config ruff.toml`——**不可带 `--force-exclude`，否则会把显式传入的文件也排除，造成"假通过"**）。**未验证**：整链 import 与真跑（`langchain`/`langgraph` 尚未安装，属 AG5 的服务依赖）。

**遗留到后续批次**：`pyproject.toml` 依赖瘦身（等 AG4 Coze import 清零后一次做，避免"声明瘦了但 import 还在"）；`db.py`/`supabase_client.py`/`s3_storage.py` 的 Coze 调用（AG3/AG4）。

### AG5 · 后端契约与反代（2026-09-23）

**动因**：客户端契约早已登记（`client/components/TabAi/TabAi.uvue:6-8`，09-01 拍板），
但后端零实现 ⇒ AI 页只能用 mock。本批把契约接到 Agent 服务。

| 文件（本仓 backend 侧） | 动作 | 说明 |
|---|---|---|
| `app/api/chat.py` | **新增** | 三端点：`POST /api/v1/chat/messages`（+ `GET /replies/{id}` + `GET /conversations/{id}/messages`） |
| `app/services/external/agent.py` | **新增** | 调 Agent 服务的唯一出口：**user_id 只由本后端注入**（agent 自身不鉴权用户身份，故不得暴露公网） |
| `app/db/models/chat.py` + 迁移 `b5c6d7e8f9a0` | **新增** | `chat_messages` 表（契约要求 `GET /replies/:id` ⇒ 回复必须可持久化；否则只能 404 或伪造） |
| `app/core/errors.py` | 加 3 码 | `CHAT_001`（agent 不可用 502）/`CHAT_002`（超时 504）/`CHAT_003`（会话或回复不存在 404）——按本仓铁律**先入登记表**再引用 |
| `app/core/config.py` | 加 3 项 | `agent_service_base_url` / `agent_service_token`（对应 agent 的 `AGENT_SERVICE_TOKEN`）/ `agent_service_timeout_s` |
| `backend/tests/test_chat.py` | **新增** | 6 例：契约形状 · 可信注入 · **失败不伪造** · 超时 504 · **会话 IDOR 404 且不调 agent** · 历史正序 |

**三条不可退让的断言**（本项目已被栽过的坑，均有专门用例）：
  1. **user_id 必须来自 JWT**（agent 侧拿到的 user_id 由本后端注入，不信客户端）；
  2. **失败不伪造回复**：agent 不可用时返回 502/504，**只留用户消息**（"问了但没答上"可复盘），绝不本地生成"看似 AI 的回答"；
  3. **会话隔离**：他人/不存在的 `conversation_id` → 404，且**不得触发 agent 调用**（防枚举、防越权写入）。

**刻意不套 `with_retry`**：本仓该装饰器对 5xx/超时重试，而对话调用**非幂等**——agent 经
`save_memory` 等工具写库，超时重试会造成**重复落库**。故单次尝试 + 明确失败（宁可报错让人重问，
也不产生脏数据）。这条已写进 `agent.py` 文件头，改动前须先读。

**未实现（诚实标注）**：SSE 真流式（需 agent 侧 SSE 输出）；`reply.kind` 的 `cards`/`confirm`
（需 agent 侧结构化输出，当前只回纯文本 ⇒ 只会产生 `bubble`/`plain`）。

### AG3 · 数据层落我们自己的 Postgres（2026-09-23）

**表结构与迁移（backend 侧）**

| 文件 | 动作 | 说明 |
|---|---|---|
| `backend/migrations/versions/a4b5c6d7e8f9_add_agent_memory_tables.py` | **新增** | 三表迁移（`memory_categories` / `memories` / `knowledge_collections`），与上游 ORM 逐列对齐；含索引与外键；带 `downgrade` |
| `backend/app/db/models/memory_agent.py` | **新增** | **ORM 镜像**（`MemoryCategory` / `Memory` / `KnowledgeCollection`）——防 `--autogenerate` 把"库里有、metadata 没有"的表判为多余而生成 **DROP TABLE**（能删数据的静默事故） |
| `backend/app/db/models/__init__.py` | 改 2 行 | 注册镜像模型（`Base.metadata` 可见） |

**迁移父节点的坑（已固化教训）**：首次把 `down_revision` 写成**按文件时间看起来最新**的 `f2a3b4c5d6e7`，
结果它与真 tip `b8c9d0e1f2a3` 分叉 ⇒ `alembic upgrade head` 报 **"Multiple head revisions are present"**。
正解：**父节点只能由 `alembic heads` 判定**（真链：`f2a3b4c5d6e7 → b2a3c4d5e6f7 → … → c8d9e0f1a2b3 → b8c9d0e1f2a3`）。

**数据层（agent 侧）**

| 文件 | 动作 | 说明 |
|---|---|---|
| `src/storage/database/local_client.py` | **新增** | **PostgREST 兼容薄层**（SQLAlchemy 支撑）：`select/eq/neq/gt/gte/lt/lte/in_/order/limit/range/maybe_single/single/insert/update/delete` + `count="exact"` + 关系嵌入 `memory_categories(name)` → 嵌套 dict |
| `src/storage/database/supabase_client.py` | **重写为入口** | 保名 `get_supabase_client()`（25+ 调用点**零改动**）+ 新增规范名 `get_client()`；移除该文件的全部 Coze/Supabase 依赖 |
| `tests/test_local_client.py` | **新增** | AG3 验收测试（5 例）：落库往返 / count / ISO 时间过滤 / 排序分页 / 关系嵌入 / 带条件更新删除 / **两条安全护栏** |

**为什么用薄层而不是逐个改写调用点**：25+ 处调用**全部经 `get_supabase_client()` 单入口**（已核），
保名替换即可零改动切换；风险集中在薄层单文件（可单测）。**代价**：多一层适配，故在文件头写明"业务逻辑不动，
替换点是薄层"，并在退场时逐批收敛（AG7 域）。

**把 PostgREST 的宽松处刻意改严**（两处，防"最贵的一类故障"）：
  ① 列名对 ORM 校验，**写错列名立即报错**（PostgREST 会 400，本地实现若不校验就静默返回空）；
  ② **无条件的 update/delete 直接拒绝**（防全表事故）。

**验证（实证）**：
  · `alembic upgrade head` 成功（`Running upgrade b8c9d0e1f2a3 -> a4b5c6d7e8f9`），heads 恢复单一；
  · schema 实证：`memory_categories` 5 列/3 索引 · `memories` 14 列/6 索引 · `knowledge_collections` 17 列/5 索引 · 外键在；
  · 读写往返实证：插入分类+记忆（JSON 列原样）→ 联表读回 → 清理；
  · `agent/tests/test_local_client.py` **5 passed**；
  · **三表零漂移**（`alembic check` 只余两条 SERIAL 序列 INFO）。
  · ⚠️ `alembic check` 整体**仍红**，但差异**全为既有漂移**（本地库 12+ 张 ORM 已不声明的遗留表 + `users`/`wechat_messages` 类型漂移）——已登记排期 **AG8**，与本批无关。

**✅ Token Plan 账号实证（同日 · 用户提供凭证后）**：最终验收**用的是 Token Plan 账号**——
key 指纹 `len=116 prefix=sk-sp- sha256[:8]=a5c42ad8`（用户 Windows 用户级环境变量），
base_url 自动推导为 **`https://token-plan.cn-beijing.maas.aliyuncs.com/compatible-mode/v1`**，
真实调用返回 `model=qwen3.8-flash finish=stop 回复='收到' reasoning_content=有(63字) 用量=66/36`。

**🔴 端点必须与 key 配对（本次最硬的坑，已固化进代码）**：官方 `error-code.md:1739` 原文——
「**套餐专属 API Key（Coding Plan / Token Plan 团队版）**…以 `sk-sp-` 开头的专属 API Key，
**必须配合各自的专属 Base URL 使用**，不可与通用 API Key/Base URL 混用（**混用会返回本鉴权错误**）」。
实测印证：`sk-sp-` key 打公共端点 `dashscope.aliyuncs.com/compatible-mode/v1` ⇒ **401 invalid_api_key**。
处置：`llm_provider` 按 key 前缀**自动推导**专属端点（sk-sp- → token-plan host；sk-ws- → 工作空间
Host + `X-DashScope-WorkSpace` header；sk- → 公共端点），并对"显式覆盖成不匹配端点"记 WARNING。

**⚠️ 账号口径更正（同日 · 用户指出）**：首次探针**误用了 `backend/.env` 的账号**——它不是 Token Plan 账号。
危害形态值得单列：**填错账号不会报错**（两边都是有效 key，只是账号 / 额度 / 业务空间不同），
探针照旧"调用成功"，于是产出的是一次**假通过**——比"报错"危险得多，因为它会被当成"配置已验证"。
修正：`scripts/probe_model.py` **删除 `backend/.env` 回退**，来源收为两处——「`agent/.env` → 进程环境变量」，
并**打印 key 与 workspace 的指纹**（长度 + 前缀 + sha256 前 8 位，不可逆、无泄漏）供每次核对账号；
凭证缺失时**显式失败**（退出码 2）而不是回退到别的账号。
