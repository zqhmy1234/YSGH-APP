# ADR-004: 端口失败至少三态，禁止空列表兼表"失败"

- 状态：accepted（2026-11-23）
- 背景：计划 §3.4；45 §6.2；深审 D05-3（检索过滤器静默丢弃）
- 决策：
  1. `SearchResult.status ∈ {ok, no_match, not_indexed, degraded}`；`MemorySnapshot.status ∈ {ok, insufficient_data, unavailable}`。
  2. 未知过滤器键 fail-closed 抛 `UnknownFilterKey`，绝不静默丢弃（尤其是 `user_id` 隔离键）。
  3. Agent 端口异常映射为 `PortTimeout` / `PortUnavailable` / `ConflictError`；空结果只表示"查过且没有"。
- 后果：
  - 好处：用户能区分"没记忆 / 没索引 / 系统挂"；跨用户召回风险被门禁拦住。
  - 代价：调用方必须处理更多分支。
- 门禁：
  - `backend/tests/agent_contract/test_l2_ports.py`
  - `agent/tests/agentv3/test_librarian.py` / `test_negative_probes.py`
  - judge mutant `degrade-not-empty`
