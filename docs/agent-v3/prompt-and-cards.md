# 提示词 Bundle 与角色卡系统（2026-11-23）

## 旧提示词复用审计

旧真源是 `agent/config/agent_llm_config.json` 的 `sp`（4 400 字符，角色「忆光」）。它不是被整体丢弃，而是**拆成能力与数据**：

| 旧内容 | 新位置 | 处理 |
|---|---|---|
| 语言限制 / 能力边界 / 拒绝话术 | `bundle._PLATFORM_GUARD` | 原意保留，提升为第一段，任何卡不得覆盖 |
| 角色定义 / 交互风格 | `prompts/persona.md`、`prompts/style.md` + `relationship.md` | 拆开，避免人设与工具规则混写 |
| V3 标签 L0–L4 / 图片分流 / 语音前缀 | `agentv3/domain/tagging.py` | **不再塞回 prompt**，改为确定性代码 + Sculptor 校验 |
| 打标 SOP / 13 工具 | `services/tools.py` + `tool_policy.md` | 8 动作 + 5 读/统计，见 `13-tools-reuse.md` |
| 保存确认格式 / 模糊检索 / 结构化浏览 | `style.md`、`tool_policy.md`；检索走 Librarian | 保留用户可见格式，检索不靠模型自由发挥 |
| 约束（不编造/不替用户决定/隐私） | `memory_policy.md` + `boundaries.md` | 保留并补危机话术 |

## 10 段 Bundle

`agent/src/agentv3/prompts/bundle.py`，固定顺序：

1. `platform_guard`（受保护，永不裁）
2. `crisis_protocol`（受保护，永不裁）
3. `character_card`
4. `relationship`
5. `boundaries`
6. `memory_policy`
7. `tool_policy`
8. `style`
9. `evidence`
10. `post_history`

预算 `BUNDLE_BUDGET = 4200` 字符；超预算按 `TRIM_PRIORITY` 先删可选段，再按比例截断可选段。**guard 与 crisis 永不删除**。`Muse` 将 bundle 作为 `system_prefix`，后面追加本轮 `Frame`（digest/evidence/task），总 system 硬限 7 900 字符。

`audit_prompt()` 在发送前检查：平台护栏存在、未超 8 000、未出现 `ignore previous` 等覆盖标记。

## Character Card V2

`agent/src/agentv3/prompts/cards.py`，遵循 Character Card V2 规范：

- 接受扁平 JSON 或 `{"spec":"chara_card_v2","spec_version":"2.0","data":{...}}` 包装。
- 字段：`name/description/personality/scenario/first_mes/mes_example/creator_notes/system_prompt/post_history_instructions/alternate_greetings/character_book/tags/creator/character_version/extensions`。
- 必填只有 `name`；`spec`/`spec_version` 不是 v2 直接 `CardError`。
- **卡是风味，不是权限**：`system_prompt` 与 `post_history_instructions` 会被 `CardSafetyPolicy` 标记为 `*_ignored`，`bundle` **不渲染**它们，平台护栏始终第一段。
- `sanitize_card()` 去控制字符、把 `ignore previous` / `忽略以上` 等替换为 `[已忽略]`。
- PNG/内嵌卡片解析**未做**（首版只做 JSON），这是已知范围边界。

## Lorebook（World Info 风格）

`activate_lorebook(book, recent_text, budget_chars, seed)`：

- `constant` 常驻；`keys` 命中；`selective` 还要 `secondary_keys` 全命中。
- `probability` 用 `sha256(seed|uid|content)` 掷骰，**确定性**，同 seed 同结果；`probability=0` 永不注入。
- `inclusion_group` 同组只取第一条（按 `order`）。
- 按 `order` 排序，超出 `budget_chars` 的条目跳过；`position`/`depth` 字段保留但当前只做 `before_char` 文本拼接。
- 参考 SillyTavern World Info：https://docs.sillytavern.app/usage/core-concepts/worldinfo/
- 许可说明：只参考字段语义，不复制 AGPL 代码；SillyTavern 为 AGPL-3.0。

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/v1/v3/cards` | 已导入卡列表（只返回 name/creator/tags） |
| POST | `/v1/v3/cards/import` | JSON 卡导入；返回 `issues`（`card_system_prompt_ignored` 等） |
| POST | `/v1/v3/cards/active` | 激活卡，热替换 `Muse.system_prefix` |

激活后 `Muse` 的 system 仍以 `## platform_guard` 开头；`test_lingqu_api.py` 用 FakeLLM 抓 `system` 断言这一点。

## 测试

- `test_prompt_bundle.py`：10 段顺序、guard 第一、预算裁剪、卡不能覆盖 guard。
- `test_card_import.py`：扁平/包装 v2、坏 spec、控制字符、safety policy。
- `test_lorebook.py`：constant、secondary keys、probability=0、确定性、inclusion group。
- `test_lingqu_api.py`：卡导入/激活、坏卡 system_prompt 被忽略。

## 参考实现

- Character Card V2：https://github.com/malfoyslastname/character-card-spec-v2
- SillyTavern：https://github.com/SillyTavern/SillyTavern（AGPL-3.0，仅参考）
- 角色卡社区格式兼容性：V2 的 `data` 包装与顶层扁平两种都接受，避免只能导入单一来源。
