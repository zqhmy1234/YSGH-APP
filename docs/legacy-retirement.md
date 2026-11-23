# 旧代码退役计划（Phase 0 · 2026-11-23）

> 目标：旧 Agent v1 与旧 backend services **有明确替代物、明确归档点、明确回滚**；
> 不把"还在用"当成"不能动"，也不把"看着没人用"当成"可以删"。

## 1. 已归档（本机，已完成）

| 旧路径 | 替代物 | 证据 |
|---|---|---|
| `agent/src/main.py` | 新 `agent/src/main.py`（`api.routes.app_from_env`） | `agent/tests/agentv3/test_openapi.py` + `pytest` 全绿 |
| `agent/src/agents/` | `agent/src/agents/`（五 Agent 新实现） | `tests/harness/test_plan_matrix.py` / 各 Agent 单测 |
| `agent/src/tools/` | `agent/src/tools/runtime.py`（只读白名单）+ Sculptor 写路径 | `test_capture_services.py` 负向：删除跨人 → 红 |
| `agent/src/storage/` | `agent/src/adapters/checkpoint.py` + `memory/ledger.py` | `test_ledger.py` / `test_integration.py` |
| `agent/src/web/api_routes.py` | backend L3 + Agent `api/routes.py` | `agent/tests/agentv3/test_api.py` |
| `agent/src/llm_provider.py` | `agent/src/adapters/lingqu/` | `lingqu/test_gateway.py`（13 profile / fallback / 预算） |
| `agent/src/utils/output_validator.py` | `agents/provenance.py` + `harness/empathy.py` | `test_provenance.py` / `tests/harness/test_empathy.py` |
| `agent/src/graphs/` | 无（空目录） | 归档时删除 |
| `agent/tests/` v1 用例 | `agent/tests/{agentv3,contract,harness}/` | `agent/pyproject.toml::testpaths` |
| `agent/src/agentv3/` 壳 | 拆到 `harness/agents/memory/prompts/ports/adapters/api/jobs/judge` | `tests/contract/test_import_boundary.py`（AST 断言） |

归档目录：`archive/legacy-agent-v1/`（含 README、git tag `legacy-agent-v1`）。
**归档目录不进 PYTHONPATH、不跑测试**；`agent/pyproject.toml` 的 `testpaths`
已显式排除。

## 2. 待归档（backend services，逐模块）

| 计划替代 | 触发条件 | 状态 |
|---|---|---|
| `backend/app/services/rag/*` → `backend/app/domain/rag` + `infra/qdrant` | SearchPort 契约测试 + 三通道口径测试通过 | 🟡 端口已定义，实现未迁移 |
| `services/events/*` → `domain/events` | TimelinePort contract + 聚合不变性测试 | 🟡 端口已定义 |
| `services/echo.py` → `domain/echo` | EchoPort 静默/排除/上限测试 | 🟡 只读 `peek_today_echo` 已接 Agent，规则未迁 |
| `services/profile_*` → `domain/profile` | Digest 三态 + ≤600 测试 | ✅ 投影规则已拆 `domain/profile/projection.py`；读模型仍走 L3 |
| `services/llm_ops/*` → `domain/human` | EmpathyChecker 单测 + 禁语单一来源 | ✅ 禁语/共情规则已拆 `domain/guard/empathy.py` |
| `services/content_purge.py` / `export.py` → `domain/trust` | 删除四层清理 + 导出截断游标 | 🟡 端口已定义，实施未开始 |

**退役顺序**：对应 domain 测试全绿 → L3 改调 port → `rg "app.services.<old>"`
零命中 → `git mv` 到 `archive/legacy-backend-services/` → 门禁全绿。
任何一步不成立就不归档（避免"先删后补"的二次漂移）。

## 3. 回滚

- Agent：`git checkout legacy-agent-v1 -- agent/src agent/tests`，按旧
  `deploy/systemd/yishu-agent.service` 启动 8300；新 staging 8400 不受影响。
- Backend：本计划未删任何 backend 文件；`backend/app/services/` 原地保留，
  回滚只需把 L3 import 改回旧 service（无数据库迁移回滚需求，B5/B7 新表不影响旧查询）。
- 数据库：新表由 `d4e5f6a7b8c9` 创建，`downgrade` 对称删除；旧表从未改写。
