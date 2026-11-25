# LiteLLM DB 模式（虚拟 Key / 预算 / 消费统计）

> 2026-11-23 已在服务器 `8.159.145.112` 上真实启用并验收。
> 本文只记录**已验证**的操作与边界；未验证的项目明确写未验证。

## 1. 它解决什么

无数据库模式（默认 `config.yaml`）只有 `LITELLM_MASTER_KEY` 一把管理员钥匙，
所有调用方共用它，无法按 Agent 分 Key、限预算、看消费。

DB 模式（`config.with-db.yaml`）增加：

| 能力 | 说明 |
|---|---|
| 虚拟 Key | `/key/generate` 生成独立 Key，按 Key 停用/删除 |
| 预算 | 每个 Key 可设 `max_budget` + `budget_duration`，超限返回 422 |
| 消费统计 | 每次调用写 `LiteLLM_SpendLogs`，按 Key/模型聚合 |
| 管理 UI | `/ui`（仅本机或 SSH 隧道；管理员用 master key 登录） |
| 模型目录入库 | `store_model_in_db: true`，模型配置可随 DB 走 |

它不改变上游模型目录本身；`model_list` 仍以 `config.yaml` 为准，DB 只是账本与 Key 管理。

## 2. 前置条件（服务器已满足）

- 服务器已有 `yishu-postgres` 容器（pgvector/pg16），只监听 `127.0.0.1:5432`
- Postgres 有库 `litellm`、角色 `litellm`
- `litellm[proxy]`、`litellm[extra_proxy]`（含 `prisma` 0.15.0）已装进 `/opt/yishu/litellm/.venv`
- Prisma CLI 5.17.0 已缓存到 `~/.cache/prisma-python/binaries/5.17.0/...`，
  且 `/root/.cache/prisma-python/nodeenv/bin/node` 可用（v26.13.1）
- DB 迁移已应用：`233 migrations`，`prisma migrate status` 显示 `Database schema is up to date!`

## 3. 一键启用（幂等）

```bash
# 在服务器上，root
bash /opt/yishu/litellm-gateway/litellm_db_enable.sh
```

脚本会：

1. 校验 venv / prisma 包 / `.database_url.saved`
2. 跑一次 `prisma generate`（生成 Python client；shipped image 通常已烘好）
3. 把 `DATABASE_URL` 写进 `/opt/yishu/litellm/.env`（600，不回显）
4. 把 `config.with-db.yaml` 安装为 `config.yaml`
5. 重启 `yishu-litellm` 并等 `/health/liveliness`
6. 备份原 `.env` 与 `config.yaml` 到 `*.pre-db-<timestamp>`

> 旧版 `setup.sh LITELLM_ENABLE_DB=1` 的失败点：它会在首次启动时现场 `npm install prisma@5.17.0`，
> 600 秒超时后留下一个没有 node 的 nodeenv 缓存，后续启动直接跳过安装并失败。
> 本目录的 helper 先跑一次 `prisma generate` 把 toolchain 预热，避免把首次启动当安装窗口。

## 4. 生成 Agent 专用 Key

```bash
MASTER=$(awk -F= '/^LITELLM_MASTER_KEY=/{sub(/^[^=]*=/,"");print}' /opt/yishu/litellm/.env)
curl -sS -X POST http://127.0.0.1:4000/key/generate \
  -H "Authorization: Bearer $MASTER" -H 'Content-Type: application/json' \
  -d '{"key_alias":"agent-v3","models":["deepseek-v4.1-flash","deepseek-v4-flash","deepseek-v4-pro","qwen3.8-flash","qwen4-max"],"max_budget":20,"budget_duration":"30d"}' \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["key"])' > /opt/yishu/litellm/agent.key
chmod 600 /opt/yishu/litellm/agent.key
```

验收（2026-11-23 实测）：

- `agent.key` 与 `LITELLM_MASTER_KEY` 不同（sha256 前缀 `9c48ff11` vs master）
- `GET /v1/models` 用 `agent.key` 返回 5 个模型
- `POST /v1/chat/completions` 用 `agent.key` 返回真实 `content`
- DB 里 `LiteLLM_VerificationToken` 有 `agent-v3` 行，`max_budget=20`、`budget_duration=30d`
- 用 `max_budget=0.000001` 的临时 Key 连续调用：第 1 次 200，第 2 次 422 `budget_exceeded` → 预算真生效
- `LiteLLM_SpendLogs` 写入 `model/total_tokens/spend/request_id`，Key 行 `spend` 同步累加

## 5. 回滚

```bash
ROOT=/opt/yishu/litellm
systemctl stop yishu-litellm
cp -a $ROOT/config.no-db.backup.yaml $ROOT/config.yaml   # 或任一 pre-db 备份
# 从 .env 删除 DATABASE_URL 行（或恢复 .env.pre-db-*）
systemctl start yishu-litellm
```

DB 库与数据不会因回滚删除；虚拟 Key 失效，但上游 Key 与模型不受影响。

## 6. 已知边界（2026-11-23）

- **公网未开放**：4000 只监听 `127.0.0.1`；远程使用走 SSH 隧道，不要让 4000 裸暴露。
- **管理 UI 登录未验证**：`/ui` 返回 200，但未在浏览器里完成 master key 登录与预算页人工走查。
- **模型目录未迁入 DB**：`LiteLLM_ProxyModelTable` 为 0 行；当前 `model_list` 仍从 YAML 读取。
  `store_model_in_db: true` 只启用后续 `/model/new` 等 DB 写入能力，不代表已有模型入库。
- **单机单实例**：LiteLLM 未配 Redis 作为共享缓存；多副本前需补。
- **DB 密码轮换**：`.database_url.saved` 与 `.env` 均为 600；轮换需同时更新两处并重启。
- **Prisma 首次启动慢**：冷启动可能 20–30 秒（迁移检查 + toolchain），systemd `Restart=always` 可兜底。
- **Prisma 连接告警**：启动日志有 `'Prisma' object has no attribute '_Prisma__engine'` 的 warning，
  但连接、Key 生成、预算、消费统计均已实测正常；升级 LiteLLM 时需复核该 warning 是否消失。
- **备份**：`/opt/yishu/litellm/.env` 与 `config.pre-db-*` 是回滚依据，建议纳入现有每日备份范围。
