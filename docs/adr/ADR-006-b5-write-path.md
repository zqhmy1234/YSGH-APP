# ADR-006: B5 写入两阶段 + 批次原子 + 用户纠正最高优先

- 状态：accepted（2026-11-23）
- 背景：设计 §5；B5-01/B5-02 任务卡
- 决策：
  1. 状态机 `draft → committed | rejected | conflict | unavailable`，模型无直接写句柄。
  2. 同批次任一 issue → 整批回滚，不落半条；`batch_hash` 幂等。
  3. 冲突优先级：用户显式纠正 > 用户确认的新事实 > 最新记录 > 高置信度；
     前三条都不满足时并列保留 `pending_confirmation`，不自动选一个。
  4. 表迁移双写为 A/B/C 阶段；旧 `agent_v3_memory_*` 保留一个发布周期。
- 后果：
  - 好处：可审计、可回滚、可解释；用户纠正不会被模型覆盖。
  - 代价：写入比单条 INSERT 复杂；夜间批处理延迟。
- 门禁：
  - `backend/tests/memory/test_conflict_rules.py`
  - `agent/tests/agentv3/test_ledger.py` / `test_sculptor.py`
  - judge mutants `correction-precedence` / `batch-idempotency` / `sculptor-refuses-bad-batch`
