# 验收证据总表（2026-11-24 更新）

> 机器可读版本：`verification-evidence.json`。原则：**没跑过的，不写成通过**；缺依赖/缺 Key 一律显式记为 `blocked` 或 `not_run_no_credential`。
>
> 2026-11-24 本轮把此前所有 `blocked` 项逐条解除或重新定性，旧数字（319/33/76 paths）已作废。

## 已真实通过

| 项 | 命令 | 结果 |
|---|---|---|
| Agent 全量套件 | `cd agent && PYTHONPATH=src .venv312/bin/python -m pytest -q` | **338 passed, 2 skipped** |
| 审判 Agent | `PYTHONPATH=src .venv312/bin/python -m judge.run` | **static P0=0 / suite exit 0 / 18/18 mutants killed / 11/11 probes ok / verdict=pass** |
| 结构门禁 | `bash scripts/dev/arch_guard.sh` | **PASS**；无 legacy import、Agent 包无 12356 |
| Backend 全量套件（真实 PG + Qdrant + Redis） | 见 §「本地 L0 环境」 | **1332 passed, 22 deselected** |
| Alembic 迁移链 | `alembic upgrade head && alembic check` | 单一 head **`e7b8c9d0e1f2`**；`No new upgrade operations detected` |
| OpenAPI 快照 | `python scripts/gen_openapi.py --check` | 一致；**79 paths** |
| L0 compose 冒烟 | `bash scripts/dev/agent_v3_compose_smoke.sh` | **6/6 PASS**（agent / catalog / PG / Redis / Qdrant / MinIO） |
| L0 真实模型探针 | `LINGQU_LIVE=1 ... compose_smoke.sh` | DeepSeek `state=ok`（~1.0s）→ `docs/agent-v3/l0-compose-smoke.json` |
| **后端→Agent 真实对话** | `POST /api/v1/chat/messages`（JWT） | **200**，真实模型回复，消息落库；同会话多轮上下文保持 |
| **Agent→后端检索** | `POST /api/v1/search/agent` | **200**，命中 1 条、`latency_ms=714`、`degraded=false`；对话引用 `[S1]` |
| **Agent→后端检索（对话内）** | 上条通过 `/chat/messages` 触发 | 回复引用真实记忆：`"去年秋天你在西湖边闻到桂花香…[S1]"` |
| **BGE-M3 本地权重** | `/app/models/bge-m3/pytorch_model.bin` | 2,271,145,830 字节（与 HF `x-linked-size` 一致）；本地加载 18.5s、dim=1024 |
| **推理模型 max_tokens 地板（本轮新修）** | DeepSeek V4.1 实测 | 带 5 个 read 工具时 `max_tokens=256` → `content_len=0`（全被隐藏推理吃掉）、400 → `finish=length` 截断、800 → 正常。已把地板改成工具感知：带工具 ≥1024，无工具 ≥256。修前**每一轮真实对话都降级成固定兜底话术** |
| **真实对话不再降级（回归）** | 连打两次 `POST /api/v1/chat/messages` | 均 **200**，`fallback=False`，回复为真实模型输出且按 persona/brief 组织 |
| **内容管线（worker）** | `POST /api/v1/contents/agent` → worker | `status: processing → done`，耗时 35.9s；Qdrant 集合 `yishu_contents` 写入 |
| 五家 live probe | `scripts/dev/lingqu_live_report.py` | DeepSeek ok；Qwen/Kimi/MiniMax/GLM 见 §阻塞 |
| LiteLLM DB 模式（服务器） | `config.db-only.yaml` 与服务端 `config.yaml` **逐字节一致** | `/v1/models` 200，5 个模型，`agent-v3` Key 预算 $20 |

## 本轮新解除的阻塞（此前记为 blocked）

| 项 | 原阻塞 | 现状 |
|---|---|---|
| 本机 Docker / Compose | 无 Docker、无 Homebrew、sudo 需密码 | **Colima 0.10.3 + Docker 29.5.2 + Compose v5.8.0 + buildx v0.30.0**，全部用户目录安装，见 `local-l0-docker.md` |
| 真 PostgreSQL 迁移 | 本机 5432 无服务 | `127.0.0.1:55432` 起 PG16+pgvector，迁移跑到 `e7b8c9d0e1f2` |
| 服务器 `docker compose` | 插件是 HTML 假文件（2318 字节） | **已替换为真实二进制 v5.8.0**（x86_64 ELF），`docker compose version` 正常 |
| BGE-M3 权重缺失导致检索降级 | `backend/models/bge-m3` 不存在，每次检索回退 HF 联网 | 权重已落盘并只读挂载进 backend/worker（`HF_HUB_OFFLINE=1`） |
| 后端镜像缺新代码 | 容器内 `users.py` 是旧版、无 chat_helpers | 重新构建；容器内 OpenAPI 79 paths，含注销/恢复/export zip/persona |
| worker 未启动 | `yishu-v3-worker` 从未创建 | 已起，`high,low` + scheduler 锁正常，真实 job 消费成功 |

## 被外部条件阻塞（未宣称通过）

| 项 | 现状 | 证据 |
|---|---|---|
| 五家厂商 live 全绿 | **partial**：DeepSeek 通过；Qwen `auth_failed`；Kimi/MiniMax/GLM 网关未配置对应模型，记 `model_or_endpoint_error` | `lingqu-live-report.json` |
| 服务器 staging 切流（8300） | 服务器 8300 仍是旧 agent（`main:app`，7 paths）；未替换生产容器 | 本轮未动，属发布动作 |
| 服务器 backend 升级 | `/opt/yishu/backend` 无 `agent_profile.py` / `memory.py` / `persona.py`，迁移只到 `f2c3d4e5a6b7` | 本轮未动，属发布动作 |
| 客户端编译 | 缺 HBuilderX | 明确降级，不宣称 |
| `tests/test_setfit.py` | 22 条按设计 deselected（需额外模型资产） | 与本轮无关 |

## 本地 L0 环境（复现用）

```
PG       127.0.0.1:55432   pgvector/pgvector:pg16
Redis    127.0.0.1:56379   redis:7-alpine
Qdrant   127.0.0.1:56333   qdrant/qdrant:v1.19.0
MinIO    127.0.0.1:59000   docker.1panel.live/bitnami/minio:latest
Agent    127.0.0.1:8300    yishu-agent-v3:local
Backend  127.0.0.1:8010    yishu-backend:local
Worker   (内部)            yishu-backend:local
```

安装步骤见 `docs/agent-v3/local-l0-docker.md`。
