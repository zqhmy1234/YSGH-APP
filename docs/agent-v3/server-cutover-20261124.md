# 服务器切换记录（2026-11-24）

> 把 `feature/agent-v3-harness@7de26ec` 的 backend + agent-v3 切到服务器，旧服务保留可回滚。
> 所有操作均可逆，备份在 `/opt/yishu/backups/`。

## 切换前

| 服务 | 版本 | 端口 |
|---|---|---|
| `yishu-api` | 旧 backend（`/opt/yishu/backend`，迁移 `c1d2e3f4a5b6`） | 8011（nginx 8010 反代） |
| `yishu-agent` | 旧 LangGraph agent（`main:app`，7 paths） | 8300 |
| `yishu-worker` | 旧 backend worker | — |
| `yishu-litellm` | DB 模式（5 模型） | 4000 |

备份：
- `/opt/yishu/backups/pre-b5b7-20261124-0513.tar.gz`（backend app+migrations）
- `/opt/yishu/backups/yishu-api.service.pre-b5b7-20261124`（原 unit）
- `/tmp/pre-b5b7.dump`（PG 全库 dump，182KB）

## 切换动作

1. **代码**：`git clone` 到 `/opt/yishu/releases/ys-7de26ec`，checkout `7de26ec`。
2. **迁移**：`alembic upgrade head` 在服务器真库执行到 **`e7b8c9d0e1f2`**
   （依次 `a7f3c2e91b44 → b8c4d2f0a1e5 → d4e5f6a7b8c9 → f6a7b8c9d0e1 → e7b8c9d0e1f2`）。
   数据无损：users 9 / contents 88 / chat_messages 2 / events 41 前后一致。
3. **模型**：`backend/models -> /opt/yishu/backend/models` 软链（复用已有 4.3G BGE-M3，不重复下载）。
4. **backend unit**：`WorkingDirectory` 指向 release 目录；新增
   `Environment=AGENT_SERVICE_BASE_URL=http://127.0.0.1:8400`。
5. **agent-v3 unit**：新增 `yishu-agent-v3.service`，跑
   `uvicorn api.routes:app_from_env --factory --app-dir .../agent/src`，端口 **8400**
   （不占用旧 8300，可随时回滚）。密钥从 `/opt/yishu/litellm/agent.key.env`（600）注入。
6. **agent venv**：`/opt/yishu/releases/ys-7de26ec/agent/.venv`，只装运行时最小集
   （fastapi / uvicorn / pydantic / httpx / litellm），不装 langchain 全家桶。

## 切换后实测

| 项 | 结果 |
|---|---|
| `yishu-api` / `yishu-agent-v3` / `yishu-litellm` / `yishu-worker` / `yishu-agent` | 全部 active |
| `GET /healthz`（8011 与 nginx 8010） | 200 |
| `GET /api/v1/persona/options` | 200（三组关系约定原文） |
| `GET/PUT /api/v1/persona`、`DELETE /users/me`、`/users/me/restore` | 已注册（未带 token 时 401/405，符合预期） |
| 真实对话 `POST /api/v1/chat/messages` | **200**，`latency 3.4s`，真实模型回复，**无降级兜底** |
| 流式 `POST /api/v1/chat/messages/stream` | **200 SSE**，`status → preview → done`，终稿落库 |
| 内容管线（新建文本 → worker） | `processing → done`，15.9s |
| 检索 `POST /api/v1/search` | **200**，`hits=3`、`degraded=false`、`latency 5.4s`，Top1 = 刚写入的那条 |
| 跨服务检索 `/api/v1/search/agent`（agent token + on-behalf-of） | **200**，命中同一内容 |
| RECALL 对话（"我上次去西湖是哪天"） | 200，回复引用"西湖边走了很久，桂花很香"，不编日期 |
| 旧 agent（8300） | 仍 active，可回滚 |

## 回滚

```bash
sudo cp /opt/yishu/backups/yishu-api.service.pre-b5b7-20261124 /etc/systemd/system/yishu-api.service
sudo systemctl daemon-reload && sudo systemctl restart yishu-api
sudo systemctl stop yishu-agent-v3 && sudo systemctl disable yishu-agent-v3
# 库回滚（仅在必须时）：
# sudo docker exec -i yishu-postgres pg_restore -U postgres -d yishu --clean /tmp/pre-b5b7.dump
```

## 未完成 / 仍需注意

1. **worker 进程仍是旧代码**：`yishu-worker.service` 的 `ExecStart` 仍指
   `/opt/yishu/backend`（venv 有 torch/rq/SenseVoice）。新代码需要这些重依赖，
   直接在 release venv 里重装会多占约 6.8G。
   **但 cron 任务已切到新代码**：`crontab` 的四条 `app.workers.maintenance`
   现在 `cd /opt/yishu/releases/ys-7de26ec/backend` 并通过旧 venv（`/opt/yishu/backend/.venv`）
   运行——venv 与代码目录解耦，`cleanup --dry-run` 与 `requeue` 均已实测 `status=ok`
   且 `account_purges` 字段存在（说明 `run_account_purges` 已生效）。
   因此 30 天到期硬删的**定时入口已经通了**，只是常驻 worker 的实时管线仍跑旧代码。
2. **旧 agent 8300 仍在跑**：内存占用约 100MB，保留观察一两天再停。
3. **回滚备份里没有 `deploy/.env`**：该文件未改，无需回滚。
