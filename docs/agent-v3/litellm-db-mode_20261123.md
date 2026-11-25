# LiteLLM DB 模式启用与验收（2026-11-23）

> 服务器：`8.159.145.112`（Ubuntu 24.04.5，Docker 29.1.3，无 compose 插件）
> 服务：`yishu-litellm.service`（`127.0.0.1:4000`）
> 用户拍板：启用 Prisma/DB 模式（虚拟 Key、预算、消费统计）；DeepSeek 临时 Key 不轮换。

## 一、结论

**DB 模式已在服务器真实启用并通过调用验收。** `agent.key` 已从管理员 Key 换成虚拟 Key
`agent-v3`（预算 20 美元 / 30 天），真实 DeepSeek 调用、预算超限拒绝、消费写入均实测通过。

未完成/未验证的边界单独列在 §六，不并入"已通过"。

## 二、为什么之前卡住

交接文档记录的失败点：`setup.sh` 在 `LITELLM_ENABLE_DB=1` 时，首次启动路径会触发
`npm install prisma@5.17.0`，在服务器 nodeenv 内超过 600 秒超时；超时后
`/root/.cache/prisma-python/nodeenv` 目录存在但 `bin/node` 不存在，而 Prisma Client Python
只按目录存在判断是否跳过安装，后续启动永久失败。

本次实际处理：

1. 先单独跑 `prisma generate`，让 nodeenv + Prisma CLI 在**服务启动之外**完成安装；
2. 确认 `prisma --version` 正常（5.17.0 / Node v26.13.1 / engines 已下载）；
3. 再切 `config.with-db.yaml` 并重启服务。

## 三、执行步骤（服务器）

```bash
# 1) 预热 Prisma toolchain（在 venv 内，不经过 LiteLLM 启动）
export PATH=/opt/yishu/litellm/.venv/bin:/root/.cache/prisma-python/nodeenv/bin:$PATH
cd /opt/yishu/litellm/.venv/lib/python3.12/site-packages/litellm/proxy
prisma generate

# 2) 确认 DB 迁移状态
export DATABASE_URL="$(sed -n 's/^DATABASE_URL=//p' /opt/yishu/litellm/.database_url.saved)"
cd /opt/yishu/litellm/.venv/lib/python3.12/site-packages/litellm_proxy_extras
prisma migrate status   # -> 233 migrations found; Database schema is up to date!

# 3) 写 DATABASE_URL 到 .env（600），安装 DB 版 config，重启
bash /opt/yishu/litellm-gateway/litellm_db_enable.sh

# 4) 生成虚拟 Key（agent-v3）并写 agent.key（600）
#    见 deploy/litellm-gateway/README.db-mode.md §4
```

操作前后均保留回滚件：

- `/opt/yishu/litellm/.env.pre-db-20261123172733`（600）
- `/opt/yishu/litellm/config.no-db.backup.yaml`
- `/opt/yishu/litellm/config.db-working.yaml`
- `/opt/yishu/litellm/.database_url.saved`（600，密码已轮换）

## 四、验收证据（2026-11-23 17:51 CST）

| 项 | 实测 |
|---|---|
| 服务状态 | `systemctl is-active yishu-litellm` → `active` |
| 健康检查 | `GET /health/liveliness` → 200 `"I'm alive!"` |
| 配置哈希 | `config.yaml` = `7e1ed8cb…`（与仓库 `config.with-db.yaml` 字节一致） |
| Prisma 迁移 | 233 个已完成，0 个失败，`migrate status` = up to date |
| 虚拟 Key | `agent.key` sha256 前缀 `9c48ff11`，与 master key **不同** |
| Key 记录 | `LiteLLM_VerificationToken`: `agent-v3 / max_budget=20 / budget_duration=30d` |
| 真实调用 | 用 `agent.key` 调 `deepseek-v4.1-flash` 返回 `content`（"正常"/"在线"/"稳定"） |
| 预算拦截 | 临时 Key `max_budget=0.000001`：第 1 次 200，第 2 次 422 `budget_exceeded` |
| 消费统计 | `LiteLLM_SpendLogs` 7 行，Key `spend` 随调用累加 |
| 管理 UI | `GET /ui/` → 200（仅本机；未做浏览器登录走查） |
| 文件权限 | `.env` / `agent.key` / `.database_url.saved` 均 `600` |

## 五、L0 compose 是什么

`deploy/compose.agent-v3.yml` 是 Agent V3 的**本地全栈冒烟环境**，不是生产部署：

| 服务 | 宿主端口 | 用途 |
|---|---|---|
| PostgreSQL(pgvector) | 55432 | 独立测试库，不碰生产 5432 |
| Redis | 56379 | RQ/缓存 |
| Qdrant | 56333/56334 | 向量库 |
| MinIO | 59000/59001 | 对象存储本地替身 |
| agent-v3 | 8300 | Lingqu + 13 工具 + 卡片 API |

它验证的是"V3 真正依赖的 L0 能不能一条命令起来"，用于替代单测之外的集成冒烟。

**当前仍 blocked**：

- 本机 macOS 无 Docker；
- 服务器有 Docker 29.1.3，但 `/usr/local/lib/docker/cli-plugins/docker-compose` 是一个
  阿里云镜像站 HTML 文件（不是可执行二进制），`docker compose` 直接报 unknown command；
- 因此 `scripts/dev/agent_v3_smoke.sh` 仍无法执行 compose 部分，`l0-smoke.json` 不能改成 pass。

修复 compose 插件属于服务器运维动作；本次未安装/覆盖系统包，避免影响现有三容器。

## 六、已知边界

1. **管理 UI 登录未走查**：`/ui/` 200 只证明静态页可访问；master key 登录、预算页、消费图未人工点验。
2. **模型未入库**：`LiteLLM_ProxyModelTable` = 0 行；`store_model_in_db: true` 只启用后续 `/model/new`
   等写入能力，当前模型仍从 YAML `model_list` 读取。
3. **单实例**：LiteLLM 未配 Redis 作为共享 cache；多副本部署前需补。
4. **启动 warning**：日志有 `'Prisma' object has no attribute '_Prisma__engine'`，但连接、Key、预算、
   消费均实测正常；升级 LiteLLM 时复核。
5. **冷启动约 20–30 秒**：迁移检查 + toolchain 准备；systemd `Restart=always` 可兜底。
6. **公网未开放**：4000 仅 `127.0.0.1`，远程仍走 SSH 隧道。
7. **DeepSeek 临时 Key**：按用户指示不轮换；仅存于服务器 `.env`（600），未写入仓库/文档/聊天。
8. **回滚**：`README.db-mode.md` §5；回滚不删除 DB 库与历史消费记录，只让虚拟 Key 失效。
