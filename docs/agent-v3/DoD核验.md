# V2 计划 §12 DoD 核验（逐条对证据，不打勾说空话）

| DoD 条目 | 状态 | 证据（可复跑） |
|---|---|---|
| `test_linger_parity.py` 全绿，19 code 有正/负例 | ✅ | `agent/tests/agentv3/test_linger_parity.py`（10 条规则）；`agent/tests/contract/test_upstream_parity_contract.py`（常量钉版）；`agent/src/agentv3/agents/sculptor.py` 的 `ISSUE_CODES` 长度断言 + `test_sculptor.py` 正/负例 |
| `test_import_boundary.py` 全绿 | ✅ | `agent/tests/contract/test_import_boundary.py` + `agent/tests/agentv3/test_boundary.py`（8 条：Agent 互不 import、memory 不依赖上层、transport 只在 adapters、契约纯净） |
| 11 条负向探针先红后绿，报告留档 | ✅ | `agent/tests/agentv3/test_negative_probes.py`（8 条旧探针）+ 3 条新增探针（guard 第一段 / 知识标签必须 ASSET / 删除按人作用域）；`judge/probes.py` 对每条做「削弱→必红→恢复→必绿」；报告 `probes.items`（11/11 ok） |
| 单轮 LLM ≤2，Muse P95 ≤4.2s，整轮 P95 ≤5.5s | ✅（假端口基线） | `test_performance.py`：50 轮断言 `cost.llm_calls ≤ 2`、整轮 P95 ≤5500ms、Muse P95 ≤4200ms。真实网络 P95 需联调环境复测，属性能门而非功能门 |
| Sculptor 双跑同 hash；apply 异常 100% 回滚 | ✅ | `test_ledger.py::test_commit_add_then_idempotent`、`test_conflict_rolls_back_whole_batch`；`test_linger_parity.py::test_rule_batch_rolls_back_and_is_idempotent`；`jobs/nightly.py` 的确定性哈希 + `test_nightly.py` 重跑幂等 |
| `python scripts/review_agent.py --full` 退出码 0 | ✅ 静态层 0；⚠️ 测试层环境受限 | `/tmp/ghvenv/bin/python scripts/review_agent.py --full --skip-tests --skip-client-compile` → **退出码 0**（569 文件语法、ruff、密钥、结构、契约、端口、安全全绿）。纯 `--full` 的 pytest 层需 `torch/sentence-transformers` 等后端重依赖（本机未装），属**环境错误**，不伪报通过 |
| `agent/docs/upstream/linger.json` 与 `NOTICE` 完整 | ✅ | `agent/docs/upstream/linger.json`（repo/commit/6 文件状态/不复用清单）；`agent/vendor/linger/upstream/`（5 个上游原文钉在 `6663e21`）+ `LOCK.json`；仓库根 `NOTICE`；`scripts/upstream_diff.py` 离线/`--online` 台账 |

## 并行于 DoD 的硬要求

| 要求 | 状态 | 证据 |
|---|---|---|
| 5 个 Agent 权限：只有 Muse 说、只有 Sculptor 写 | ✅ | `test_registry.py`（`may_write` 只有 sculptor、`may_speak` 只有 muse、越权抛 PermissionError） |
| 高解耦：零跨 Agent import、transport 只在 adapters | ✅ | `test_boundary.py` + `test_import_boundary.py` |
| 记忆三层 + 修正层 + 双时间 + 幂等 + 回滚 | ✅ | `test_ledger.py`（12 条）、`test_projection.py`（三态） |
| 用户修正 > AI（四处闭环） | ✅ | 拒写（propose）/整批回滚（commit）/不裁（frame）/reject（provenance）各一条测试 |
| 降级诚实：partial+gaps、LLM 故障固定话术 | ✅ | `test_librarian.py`、`test_orchestrator.py` |
| API：7 个 v3 端点 + `/v1/chat` 兼容 | ✅ | `test_api.py`（11 条）：turn/digest/memory/purge/ingest/echo/family + 401/422 分支 |
| 夜间批次可跑且幂等 | ✅ | `jobs/nightly.py` + `test_nightly.py`（6 条） |
| PG 记忆表迁移 + 适配器 | ✅（新增表 + JSONB 单文档） | `backend/migrations/versions/a7f3c2e91b44_add_agent_v3_memory.py`（6 表）；`adapters/pg_memory.py` + `test_pg_memory.py`（假连接测同事务/回读/幂等/缺驱动降级） |
| 回放与检索评测 | ✅ | `golden/replay_200.json`（200 条）；`test_locomo.py` 在真实 LoCoMo-10（1982 条问答）上跑 recall@10=0.566、MRR=0.376 |
| 45 需求归属 | ✅（有主，非全实现） | `docs/agent-v3/45需求覆盖矩阵.md`：15 ✅ / 17 🟡 / 13 ❌，❌ 均属端侧或二期 |
