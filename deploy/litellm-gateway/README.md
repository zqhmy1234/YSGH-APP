# LiteLLM 网关（公司统一模型入口）

给忆述光华及后续 Agent 用的 OpenAI 兼容模型网关。它负责：

- 统一入口：客户端只认 `http://127.0.0.1:4000/v1`（远程走 SSH 隧道）
- 供应商 Key 托管：DeepSeek / 通义的真 Key 只在服务器
- 模型别名与 fallback：DeepSeek 失败时回退到通义
- 以后公司换成自己的统一网关，只改 `COMPANY_LLM_BASE_URL`，客户端代码不动

## 两种模式

| 模式 | 配置 | 能力 | 适用 |
|---|---|---|---|
| 无 DB（默认） | `config.yaml` | 模型转发；`agent.key` 就是管理员 Key | 只做内网转发、不关心分 Key/预算 |
| DB 模式 | `config.with-db.yaml` | 虚拟 Key / 每 Key 预算 / 消费统计 / 管理 UI | Agent 上线、需要按调用方隔离成本 |

**2026-11-23 起服务器已启用 DB 模式**，完整操作与回滚见
[`README.db-mode.md`](README.db-mode.md)。

## 文件

| 文件 | 用途 |
|---|---|
| `config.yaml` | 默认无数据库配置（模型目录、fallback） |
| `config.with-db.yaml` | 启用虚拟 Key/预算时使用 |
| `.env.example` | 环境变量模板（不含真 Key） |
| `setup.sh` | 服务器安装（venv + systemd；DB 模式另建库） |
| `litellm_db_enable.sh` | **DB 模式一键启用**（预热 Prisma toolchain + 写 DATABASE_URL + 切配置） |
| `README.db-mode.md` | DB 模式已验证的操作、验收与回滚 |
| `yishu-litellm.service` | systemd 单元 |

## 模型别名

| 客户端 model | 上游真实 model | 说明 |
|---|---|---|
| `deepseek-v4.1-flash` | `deepseek-flash` | DeepSeek V4.1 Flash，默认测试模型 |
| `deepseek-v4-flash` | `deepseek-flash` | 兼容旧 Lingqu catalog 的别名 |
| `deepseek-v4-pro` | `deepseek-v4-pro` | DeepSeek V4 Pro |
| `qwen3.8-flash` | `qwen3.8-flash` | 通义，fallback |
| `qwen4-max` | `qwen4-max` | 通义，fallback |

注意：DeepSeek 官方 `/models` 返回的 id 是 `deepseek-flash`，不是 `deepseek-v4-flash`；不要直接照计划里的名字调官方 API。

## 部署

前提：服务器已有 `yishu-postgres` 容器（仅 DB 模式需要）、Python 3.10–3.13、root 权限。

```bash
scp -r deploy/litellm-gateway root@<server>:/opt/yishu/
cd /opt/yishu/litellm-gateway
sudo bash setup.sh
```

脚本会：

1. 建 `/opt/yishu/litellm/.venv` 并安装 `litellm[proxy]`
2. 生成 `LITELLM_MASTER_KEY`、`LITELLM_SALT_KEY`，写入 `/opt/yishu/litellm/.env`（600）
3. 安装并启动 `yishu-litellm.service`
4. 等 `/health/liveliness` 通过
5. 写 `/opt/yishu/litellm/agent.key`（600）

不会把 Key 打印到终端。

## 验证

```bash
curl -s http://127.0.0.1:4000/health/liveliness
curl -s http://127.0.0.1:4000/v1/models \
  -H "Authorization: Bearer $(cat /opt/yishu/litellm/agent.key)"
```

真实调用：

```bash
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $(cat /opt/yishu/litellm/agent.key)" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"你好"}],"max_tokens":256}'
```

DeepSeek V4.1 Flash 默认会先输出 reasoning，`max_tokens` 太小会只拿到 reasoning、`content` 为空；测试至少给 256。

## 给 Agent 用

### 同机 Agent

```bash
COMPANY_LLM_BASE_URL=http://127.0.0.1:4000/v1
COMPANY_LLM_API_KEY=$(cat /opt/yishu/litellm/agent.key)
```

### 远程 Agent

默认只监听 `127.0.0.1`，推荐 SSH 隧道，不要把 4000 裸暴露公网：

```bash
ssh -L 4000:127.0.0.1:4000 root@<server>
# 客户端仍用 http://127.0.0.1:4000/v1
```

如果要公网访问，必须再加 nginx + TLS + IP 白名单，不能直接 `http://IP:4000` 明文传 Key。

## 加 DeepSeek Key

```bash
sudo sed -i 's|^DEEPSEEK_API_KEY=.*|DEEPSEEK_API_KEY=<key>|' /opt/yishu/litellm/.env
sudo systemctl restart yishu-litellm
```

不要把 Key 贴进聊天、git 或命令历史；临时 Key 测完要轮换。

## 启用虚拟 Key/预算

**推荐**：用 `litellm_db_enable.sh`（已处理 Prisma 首次安装超时问题）：

```bash
sudo bash /opt/yishu/litellm-gateway/litellm_db_enable.sh
```

旧路径 `sudo LITELLM_ENABLE_DB=1 bash setup.sh` 的已知问题：LiteLLM 首次启动现场
`npm install prisma@5.17.0`，600 秒超时后 nodeenv 留下不完整缓存，后续启动永久跳过安装。
本目录的 helper 会先跑 `prisma generate` 预热，再切换配置；完整步骤见 `README.db-mode.md`。

## 运维

```bash
systemctl status yishu-litellm
tail -50 /var/log/yishu-litellm.log
```

- `.env` 的 `LITELLM_SALT_KEY` 必须备份：换掉后 DB 模式里已加密的供应商 Key 会读不出来。
- **DB 模式**下 `agent.key` 是虚拟 Key（`agent-v3`，有预算与消费统计），不是管理员 Key；
  master key 只留在 `.env`，仅用于 `/key/*`、`/model/*` 管理操作。
- 无 DB 模式下 `agent.key` 才是管理员 Key；两种模式不要混用同一份 `agent.key`。
- Postgres 只监听 `127.0.0.1:5432`，不要对公网开放。
- DB 模式回滚：`README.db-mode.md` §5。
