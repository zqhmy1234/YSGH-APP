# ADR-001: Agent 是外部消费者，只走 HTTP + shared/contracts

- 状态：accepted（2026-11-23）
- 背景：`docs/架构重构总计划_v3_20261123.md` §0.1、§0.3 硬规则 2/4
- 决策：
  1. `agent/` 不 import `backend/`；`backend/` 不 import `agent/`。
  2. 跨进程 DTO 只放 `shared/contracts/`；Agent 的 `BackendPort` 只映射六个 L3 端点。
  3. Agent 不持 Qdrant/COS 凭据，不直连业务表；checkpointer 放 Agent 自己的 schema。
- 后果：
  - 好处：两侧可独立发布；Agent 无法绕过鉴权/审计直接改库。
  - 代价：每次跨进程调用多一跳；需要契约一致性测试。
- 门禁：
  - `agent/tests/contract/test_import_boundary.py`（AST 断言无 backend import / 无传输库外泄）
  - `backend/tests/agent_contract/test_shared_contract_parity.py`（DTO 字段逐一对齐）
  - `backend/tests/agent_contract/test_l2_ports.py`（15 端口、无传输库 import）
