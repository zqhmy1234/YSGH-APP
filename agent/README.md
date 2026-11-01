# 忆述光华 Agent 服务

Agent 是 backend 内部调用的独立进程。对外入口只有 backend 的
`POST /api/v1/chat/messages` / `/messages/stream`；Agent 自身监听 `8300`，
不得发布到公网。

服务边界与依赖：

- `backend` 负责用户 JWT、限流、会话持久化、输出护栏和 Agent 反代；
- Agent 只接受 backend 注入的 `user_id`，并用 `AGENT_SERVICE_TOKEN` 校验服务间请求；
- 三张记忆表由 backend 的 Alembic 拥有并创建，Agent 不建生产表；
- 模型通过 OpenAI 兼容接口调用，不在本地加载模型。

## 推荐启动方式：Docker

在仓库根目录执行。该路径不要求宿主安装 Python，也与部署镜像一致。

```powershell
Copy-Item deploy/.env.production.template deploy/.env
# 编辑 deploy/.env，至少填写：
# POSTGRES_PASSWORD
# AGENT_SERVICE_TOKEN
# AGENT_DASHSCOPE_API_KEY（或单独配置的 AGENT_LLM_*）

docker compose -f deploy/docker-compose.yml --profile app up -d --build postgres agent
docker compose -f deploy/docker-compose.yml ps agent
docker compose -f deploy/docker-compose.yml exec agent curl -fsS http://127.0.0.1:8300/health
```

`AGENT_SERVICE_TOKEN` 必须与 backend 侧同名变量一致。Agent 的模型 key
必须来自独立账号，不能与 backend 的 `DASHSCOPE_API_KEY` 混用。

启动完整的 App 后端、worker 与 Agent：

```powershell
docker compose -f deploy/docker-compose.yml --profile app up -d --build
```

## 本地源码运行

前置条件：Python 3.12 与 [uv](https://docs.astral.sh/uv/)。

```powershell
Set-Location agent
uv sync --frozen --dev --python 3.12
Copy-Item .env.example .env
# 编辑 .env
.\scripts\local_run.ps1
```

POSIX shell：

```bash
cd agent
uv sync --frozen --dev --python 3.12
cp .env.example .env
./scripts/local_run.sh
```

本地单独运行 Agent 时，backend 需要使用相同 token，并指向
`http://127.0.0.1:8300`。如果复用 backend RAG，还要设置
`AGENT_BACKEND_BASE_URL`。

## 测试

单元测试可在没有数据库时运行，但真实 PostgreSQL 集成测试需要隔离测试库。
测试库名称必须包含 `test`，初始化脚本会拒绝连接其他数据库。

```powershell
# 先启动一个隔离 PostgreSQL，再设置 DATABASE_URL
$env:DATABASE_URL = "postgresql+psycopg://postgres:<PASSWORD>@127.0.0.1:5432/yishu_agent_test"
$env:AGENT_SERVICE_TOKEN = "<LOCAL_TEST_TOKEN>"
$env:PYTHONPATH = "src"

.\.venv\Scripts\python.exe scripts\init_test_db.py
.\.venv\Scripts\python.exe -m pytest tests -q
```

仓库 CI 使用独立 PostgreSQL service 和同一个初始化脚本。测试失败时不要用
“历史 194 passed”替代当日结果。

## 接口

| 方法 | 路径 | 说明 |
|---|---|---|
| `GET` | `/health` | 服务、数据库、鉴权、checkpointer 与降级状态 |
| `POST` | `/v1/chat` | 非流式对话，仅供 backend 调用 |
| `POST` | `/v1/chat/stream` | SSE：`status / delta / tool / replace / done / error` |
| `GET` | `/api/dashboard` | 记忆管理面 |
| `GET` | `/api/memories` | 记忆列表与筛选 |
| `GET` | `/api/collections` | 知识收藏列表 |
| `GET` | `/api/categories` | 记忆分类 |

管理面同样要求 `X-Agent-Token`，但仍不得发布到公网。

## 配置与排障

- 完整环境变量模板：`.env.example`
- 数据层与历史改造记录：`UPSTREAM.md`
- 部署文件：`../deploy/Dockerfile.agent`、`../deploy/docker-compose.yml`
- 模型实调与凭证指纹：`scripts/probe_model.py`
- 端口只认 `AGENT_HTTP_PORT`，默认 `8300`；不存在旧的 `-p 5000` 用法

启动前确认：

1. `DATABASE_URL` 指向已有 backend schema 的数据库；
2. backend 与 Agent 的共享 token 同值；
3. 模型 provider、key 与 base URL 来自同一账号；
4. `/health` 的 `db_configured`、`auth_enabled`、`checkpointer` 状态符合预期；
5. 经 backend 发起一次真实对话后，再宣称链路可用。

