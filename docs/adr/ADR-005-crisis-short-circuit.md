# ADR-005: T3 在 L3/L1 短路，0 次 LLM；Agent 包不得出现资源号/联络声明

- 状态：accepted（2026-11-23）
- 背景：设计 §12.4；45 §5.6；B7-04
- 决策：
  1. T3 由 backend L3 在调 Agent 前短路：固定话术 + 12356/急救 + 明示 AI 身份。
  2. `crisis_events` 只留 `level/record_status/escalation_note`，**无"已联络"字段**。
  3. Agent 源码不得出现 `12356` / `handoff` 实现；危机提示不拼角色卡。
- 后果：
  - 好处：危机不依赖模型可用性；不会出现"AI 说已联络"的虚假承诺。
  - 代价：L3 与 Agent 各有一份安全话术；由门禁保证不漂移。
- 门禁：
  - `backend/tests/crisis/test_crisis.py`
  - `agent/tests/harness/test_crisis.py`（源码无 12356）
  - judge mutant `crisis-short-circuit`
