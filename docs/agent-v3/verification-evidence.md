# 验收证据总表（2026-11-23）

> 机器可读版本：`verification-evidence.json`。原则：**没跑过的，不写成通过**；缺依赖/缺 Key 一律显式记为 `blocked` 或 `not_run_no_credential`。

## 已真实通过

| 项 | 命令 | 结果 |
|---|---|---|
| Agent 全量套件 | `cd agent && PYTHONPATH=src .venv312/bin/pytest tests/agentv3 tests/contract -q` | **253 passed, 2 skipped**（2 条 live 测试按设计跳过） |
| 审判 Agent | `PYTHONPATH=src .venv312/bin/python -m agentv3.judge.run` | **static P0=0 / suite exit 0 / 18/18 mutants killed / 11/11 probes ok / verdict=pass** |
| 全量静态门禁 | `PATH=agent/.venv312/bin:$PATH python scripts/review_agent.py --full --skip-tests --skip-client-compile` | 退出码 0；语法 602 文件、lint、密钥、结构、契约轴、端口、安全全绿 |
| 20 场脚本对话（确定性） | `ROLEPLAY_ENGINE=fake ... run_live_cases.py` | **20/20 pass**，报告明确 `mode=fake`，不冒充 live |
| 模型目录 | `PYTHONPATH=src .venv312/bin/python -m agentv3.lingqu.cli catalog-check` | `5 providers / 13 profiles / 默认 deepseek/deepseek-v4-flash` |

## 被外部条件阻塞（未宣称通过）

| 项 | 现状 | 证据 |
|---|---|---|
| L0 compose 启动 | **blocked: docker_missing**。本机与云主机均无 Docker/Compose，只完成 YAML 解析与 smoke 语法检查 | `l0-smoke.json` |
| 五家厂商 live | **not_run_no_credential**。`COMPANY_LLM_API_KEY` 未提供，5/5 记 `skipped_no_credential` | `lingqu-live-report.json` |
| 20 场 DeepSeek 真实对话 | **not_run_no_credential**。脚本与 20 个 case 已就绪，20/20 记 skipped | `roleplay-20-live-report.json` |
| `review_agent --full` 测试层 | **环境错误**：本机 venv 未装 backend 重依赖，backend pytest 116+ ERROR；静态层已全绿 | `.cowork-temp/test-report.json` |

## 解除阻塞所需

1. 一台有 Docker Engine/Compose 的机器（本机需装 Docker Desktop 或 Colima），然后 `bash scripts/dev/agent_v3_smoke.sh`。
2. `COMPANY_LLM_API_KEY`（或逐家 `LINGQU_<PROVIDER>_API_KEY`）注入运行环境，然后跑：
   - `python scripts/dev/lingqu_live_report.py`
   - `python agent/tests/agentv3/roleplay/run_live_cases.py`
3. 安装 `backend/requirements.txt` 后重跑 `review_agent.py --full`，补齐后端测试层。
