# 交接：LiteLLM 网关落地与当前完整状态

生成：2026-11-23 16:05 CST
仓库：`/Users/silver/GHapp`，分支 `feature/agent-v3-harness`
服务器：`8.159.145.112`（Ubuntu 24.04.5，8C/14G，Docker 29，无 compose 插件）

## 一句话

**本地已验证、已上传、服务器上已跑通真实 DeepSeek V4.1 Flash 的 LiteLLM 网关；当前是“无数据库模式”，`agent.key` 即管理员 Key。虚拟 Key/预算所需的 Prisma+Postgres 模式已搭了一半，卡在 Prisma 的 npm 安装，未启用。**

## 已经真实通过的

### 本地

- 本地专用 venv：`/tmp/litellm-local-venv`（uv 建，Python 3.12）
- 本地配置：`/tmp/litellm-local.yaml`（从 `deploy/litellm-gateway/config.yaml` 去掉 DB 配置）
- 本地进程监听 `127.0.0.1:4001`
- `GET /health/liveliness` → 200 `"I'm alive!"`
- `GET /v1/models` → 5 个模型
- `POST /v1/chat/completions`，model=`deepseek-v4.1-flash` → `content='网关成功'`

### 服务器

- 网关目录：`/opt/yishu/litellm`
- systemd：`yishu-litellm.service`，`active (running)`
- 监听：`127.0.0.1:4000`（只本机，未裸暴露公网）
- 健康：`curl http://127.0.0.1:4000/health/liveliness` → 200
- 模型列表：`deepseek-v4.1-flash`、`deepseek-v4-flash`、`deepseek-v4-pro`、`qwen3.8-flash`、`qwen4-max`
- 真实调用：model=`deepseek-v4.1-flash` → `content='网关成功'`，usage 102 tokens
- 访问 Key：`/opt/yishu/litellm/agent.key`（600，sha256 前缀 `a9089ee4`）
- 上游 Key：`/opt/yishu/litellm/.env`（600），DeepSeek 临时 Key 已写入
- 环境文件：`/opt/yishu/litellm/.env`
- 日志：`/var/log/yishu-litellm.log`
- 配置备份：`/opt/yishu/litellm/config.with-db.yaml`（带 DB 版）

### 关键事实：DeepSeek 真实模型名

用临时 Key 请求 `https://api.deepseek.com/models` 得到：

| 官方 id | 展示名 | 上下文 | 最大输出 |
|---|---|---|---|
| `deepseek-flash` | DeepSeek-V4.1-Flash | 1048576 | 393216 |
| `deepseek-v4-pro` | DeepSeek-V4-Pro | 1048576 | 393216 |

**计划里的 `deepseek-v4-flash` 在官方 API 不存在。** 网关用别名映射：

- `deepseek-v4.1-flash` → 上游 `deepseek-flash`
- `deepseek-v4-flash` → 上游 `deepseek-flash`（兼容旧 Lingqu catalog）
- `deepseek-v4-pro` → 上游 `deepseek-v4-pro`

DeepSeek V4.1 Flash 默认先输出 reasoning；`max_tokens` 小于约 100 时可能只拿到 reasoning、`content` 为空。测试至少给 256。

### 本地/仓库产物

`deploy/litellm-gateway/`：

- `config.yaml` — 默认无 DB，5 个模型别名、fallback
- `config.with-db.yaml` — 启用虚拟 Key/预算时用
- `.env.example` — 环境变量模板
- `setup.sh` — 服务器安装；默认 `LITELLM_ENABLE_DB=0`
- `yishu-litellm.service` — systemd 单元
- `README.md` — 部署、验证、Agent 接入、切 DB 模式说明

## 没做完的、以及为什么

### 1. 虚拟 Key / 预算未启用

- 已创建 Postgres 库 `litellm` 和角色 `litellm`
- 已安装 `prisma` Python 包（0.15.0）
- 但 LiteLLM 启动时调用 `npm install prisma@5.17.0`（nodeenv 内），在服务器上极慢/卡住，超过 600 秒
- 为不阻塞交付，当前把 `DATABASE_URL` 注释掉，走无 DB 模式；`agent.key` 就是 master key
- 后果：**没有按 Agent 的独立 Key、预算、消费统计**；内部受信 Agent 可用，不能当公网多租户网关

要补时：

```bash
# 方案 A：修 npm 镜像后启用
export PATH=/root/.cache/prisma-python/nodeenv/bin:$PATH
npm config set registry https://registry.npmmirror.com
sudo LITELLM_ENABLE_DB=1 bash /opt/yishu/litellm-gateway/setup.sh

# 方案 B（更稳）：改用官方 LiteLLM Docker 镜像，跳过 pip/prisma
```

DB URL 暂存在 `/opt/yishu/litellm/.database_url.saved`（600）。

### 2. 公网访问未开

- 现在只监听 `127.0.0.1:4000`
- 远程 Agent 走 SSH 隧道：

```bash
ssh -L 4000:127.0.0.1:4000 root@8.159.145.112
```

- 如果要公网：必须 nginx + TLS + IP 白名单；不能直接 `http://8.159.145.112:4000` 明文传 Key

### 3. 安全动作（必须做）

- 用户提供的 DeepSeek 临时 Key 已出现在聊天里，**测试完成后必须去 DeepSeek 控制台轮换/删除**
- `.env` 里的 Key 不要回显、不要提交、不要贴群
- `agent.key` 在无 DB 模式下等于管理员 Key，只给受信 Agent，不给客户端/前端

### 4. 与 Lingqu 的接线未做

- Lingqu catalog 仍用 `deepseek/deepseek-v4-flash`；网关保留了 `deepseek-v4-flash` 别名，所以能通
- 但默认测试应按用户要求改成 `deepseek-v4.1-flash`
- 建议改 `agent/config/lingqu-catalog.json`：
  - 默认 profile id 改 `deepseek/deepseek-v4.1-flash`
  - `model` 改 `deepseek-v4.1-flash`
  - 如果走公司网关，`litellm_model` 用 `openai/deepseek-v4.1-flash`，base 指网关
- 改完跑：`PYTHONPATH=src .venv312/bin/pytest tests/agentv3 tests/contract -q`

### 5. L0 与 20 场真实对话仍未跑

- `docs/agent-v3/l0-smoke.json` 仍是 `blocked/docker_missing`
- 20 场 live 仍是 `not_run_no_credential`
- 现在有了网关和 DeepSeek Key，可以先跑 Lingqu 的 `probe`，再跑 20 场
- 注意：不要把网关的 `agent.key` 直接写进仓库；用服务器环境变量或本地临时 env

## 服务器现状（不要误改）

已有服务/容器：

- systemd：`yishu-api.service`（127.0.0.1:8011）、`yishu-agent.service`（127.0.0.1:8300）、`yishu-worker.service`、`nginx`
- Docker：`yishu-postgres`（127.0.0.1:5432）、`yishu-redis`（6379）、`yishu-qdrant`（6333/6334）
- 8010 由 nginx 监听，转发到 8011

新增：

- `yishu-litellm.service` → `127.0.0.1:4000`

端口 4000 之前空闲，未与现有服务冲突。

## 可直接用的命令

```bash
# 健康
curl -s http://127.0.0.1:4000/health/liveliness

# 模型列表
curl -s http://127.0.0.1:4000/v1/models \
  -H "Authorization: Bearer $(sudo cat /opt/yishu/litellm/agent.key)"

# 真实对话
curl -s http://127.0.0.1:4000/v1/chat/completions \
  -H "Authorization: Bearer $(sudo cat /opt/yishu/litellm/agent.key)" \
  -H 'Content-Type: application/json' \
  -d '{"model":"deepseek-v4.1-flash","messages":[{"role":"user","content":"只回复四个字：网关成功"}],"max_tokens":256,"temperature":0}'
```

## 下一步优先级

1. 轮换聊天里暴露的 DeepSeek Key，写回 `.env`，重启 `yishu-litellm`
2. 把 Lingqu 默认 profile 改到 `deepseek-v4.1-flash`，跑 `probe`
3. 用网关跑 20 场真实对话，产出 `roleplay-20-live-report.json`
4. 再决定是否投入 Prisma/DB 模式（虚拟 Key、预算）；不投入也能内部用
5. L0 真启动仍需要 Docker/Compose 或能跑 rootless 的机器，和网关是两件事
