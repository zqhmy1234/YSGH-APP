# 验收证据总表（2026-11-23）

> 机器可读版本：`verification-evidence.json`。原则：**没跑过的，不写成通过**；缺依赖/缺 Key 一律显式记为 `blocked` 或 `not_run_no_credential`。

## 已真实通过

| 项 | 命令 | 结果 |
|---|---|---|
| Agent 全量套件 | `cd agent && PYTHONPATH=src .venv312/bin/pytest tests/agentv3 tests/contract -q` | **258 passed, 2 skipped**（2 条 live 测试按设计跳过） |
| 审判 Agent | `PYTHONPATH=src .venv312/bin/python -m agentv3.judge.run` | **static P0=0 / suite exit 0 / 18/18 mutants killed / 11/11 probes ok / verdict=pass**（judge 计数含 2 条 live 测试，合计 260 passed） |
| 全量静态门禁 | `PATH=agent/.venv312/bin:$PATH python scripts/review_agent.py --full --skip-tests --skip-client-compile` | 退出码 0；语法 602 文件、lint、密钥、结构、契约轴、端口、安全全绿 |
| 20 场脚本对话（确定性） | `ROLEPLAY_ENGINE=fake ... run_live_cases.py` | **20/20 pass**，报告明确 `mode=fake`，不冒充 live |
| 20 场真实对话（DeepSeek 网关） | `COMPANY_LLM_BASE_URL=http://127.0.0.1:4001/v1 ... run_live_cases.py` | **20/20 pass**，真实模型调用；c05/c06 为确定性护栏短路（0 LLM calls），其余为真实调用 |
| 五家 live probe | `PYTHONPATH=agent/src ... scripts/dev/lingqu_live_report.py` | **DeepSeek ok；Qwen auth_failed；Kimi/MiniMax/GLM model_or_endpoint_error**，报告 `partial`，不再伪称全部未跑 |
| 模型目录 | `PYTHONPATH=src .venv312/bin/python -m agentv3.lingqu.cli catalog-check` | `5 providers / 13 profiles / 默认 deepseek/deepseek-v4.1-flash` |
| LiteLLM DB 模式（服务器） | 见 `docs/agent-v3/litellm-db-mode_20261123.md` | **已启用**：233 迁移、`agent-v3` 虚拟 Key（预算 $20/30d）、真实调用、预算 422、消费日志均实测通过 |

## 被外部条件阻塞（未宣称通过）

| 项 | 现状 | 证据 |
|---|---|---|
| L0 compose 启动 | **blocked: compose 不可用**。本机 macOS 无 Docker；服务器有 Docker 29.1.3，但 `docker compose` 插件文件是阿里云镜像站 HTML（非二进制）。只完成 YAML 解析与 smoke 语法检查 | `l0-smoke.json`、`litellm-db-mode_20261123.md` §5 |
| 五家厂商 live | **partial**：DeepSeek 通过；Qwen `auth_failed`；Kimi/MiniMax/GLM 网关未配置对应模型，记 `model_or_endpoint_error` | `lingqu-live-report.json` |
| `review_agent --full` 测试层 | **环境错误**：本机 venv 未装 backend 重依赖，backend pytest 116+ ERROR；静态层已全绿 | `.cowork-temp/test-report.json` |

## 解除阻塞所需

1. 一台有可用 Docker Engine + Compose 的机器（本机装 Docker Desktop/Colima；服务器需修复 `docker-compose` 插件），然后 `bash scripts/dev/agent_v3_smoke.sh`。
2. 若要五家全绿：在网关 `config.yaml` 增加 Qwen/Kimi/MiniMax/GLM 的可用上游与 Key，再用 `python scripts/dev/lingqu_live_report.py` 复跑。
3. 安装 `backend/requirements.txt` 后重跑 `review_agent.py --full`，补齐后端测试层。
