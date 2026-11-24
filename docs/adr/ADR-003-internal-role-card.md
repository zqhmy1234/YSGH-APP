# ADR-003: 运行时只用内部 RoleCard；Character Card V2 仅导入导出

- 状态：accepted（2026-11-23）
- 背景：计划 §5.4/§5.5；设计 §13；决策台账 §29
- 决策：
  1. `prompts/role_card.py::RoleCard` 是唯一运行时对象，12 张内部卡放 `agent/assets/cards/`。
  2. `system_prompt` / `post_history_instructions` / `creator_notes` 永不渲染。
  3. `character_book` 只取命中 lore（≤200 字），标注"设定参考，不是用户事实"，不写记忆。
  4. V2 适配只在 `prompts/card_v2.py`；下载的第三方卡只放 `research/character-cards/` 研究。
- 后果：
  - 好处：卡作者无法把题材规则/世界观带进产品；卡可审计（schema/长度/taboo/hash）。
  - 代价：V2 导入需要适配层；第三方卡不能直接上线。
- 门禁：
  - `agent/tests/harness/test_role_card.py`（12 卡/未知字段/无 system_prompt/稳定 hash）
  - `agent/tests/agentv3/test_prompt_assets.py`（V2 示例卡安全）
  - judge `card-cannot-move-platform-guard` mutant
