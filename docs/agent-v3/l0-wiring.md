# L0 真接线（2026-11-23）

## 一条命令

```bash
docker compose -f deploy/compose.agent-v3.yml up -d --build
bash scripts/dev/agent_v3_smoke.sh
docker compose -f deploy/compose.agent-v3.yml down
```

`deploy/compose.agent-v3.yml` 只绑 `127.0.0.1`，服务：

| 服务 | 端口（宿主） | 用途 |
|---|---|---|
| `postgres` | 55432 | pgvector:pg16；`deploy/initdb` 首次建 vector 扩展 |
| `redis` | 56379 | RQ 队列后端；AOF + noeviction |
| `qdrant` | 56333 / 56334 | named vectors 存储；v1.19.0 与 backend client 对齐 |
| `minio` | 59000 / 59001 | COS 本地替身；S3 兼容 |
| `agent-v3` | 8300 | Lingqu + 13 工具 + 提示词/角色卡 API |

可选 `--profile app` 追加 `backend` / `worker`：复用 `deploy/Dockerfile.backend`，`AGENT_SERVICE_BASE_URL=http://agent-v3:8300`。默认 profile 不启动它们，因为 V3 的模型/卡片 API 不依赖 backend，smoke 可以先验 L0。

## 镜像

`deploy/Dockerfile.agentv3`：

- 独立于旧 `Dockerfile.agent`（旧服务入口是 `main:app`，V3 是 `agentv3.api.routes:app_from_env --factory`）。
- `uv sync --frozen --no-dev`，`litellm` 已写入 `agent/pyproject.toml` + `uv.lock`。
- 非 root（uid 10002），内置 `HEALTHCHECK /health`。

## smoke 判定

`scripts/dev/agent_v3_smoke.sh` 逐项 PASS/FAIL：

1. agent `/health`
2. `/v1/v3/models`：5 providers、默认 `deepseek/deepseek-v4-flash`
3. postgres `pg_isready`
4. redis `PING/PONG`
5. qdrant `/readyz`
6. minio `/minio/health/live`
7. `LINGQU_LIVE=1` 且有 key 时才发 DeepSeek 真实 probe；否则打印 `SKIP`，不伪造 pass。

## 本机验证状态（诚实记录）

- 本机 macOS **没有安装 Docker**（`docker: command not found`），所以 compose **未实际 up**。
- 已做：`bash -n` 语法检查 + PyYAML 解析 + `npx` 不适用；Docker 镜像构建/服务连通性均未运行。
- 没有 `COMPANY_LLM_API_KEY`，真实 DeepSeek/Qwen/Kimi/MiniMax/GLM 请求均未发出。
- 因此「L0 真接线」当前状态是**代码与脚本落地、未在本机运行验证**；有 Docker 的机器上跑上面的三条命令即可得到 PASS/FAIL 证据。

## 已知边界

- MinIO 没有健康检查（官方镜像不一定带 curl）；改为 `service_started`，smoke 用宿主 HTTP 探活。
- `backend`/`worker` 的 `app` profile 没有挂模型卷；需要完整管线时按 `deploy/RUNBOOK.md` 挂 `backend/models`。
- `AGENTV3_MEMORY_PATH` 用文件 SQLite；多副本部署前必须改为共享 PG 账本（`PgMemoryStore` 已实现，未在 compose 中启用）。
