# card.md — 人可读的角色卡中间格式 / A human-readable intermediate format for character cards

`card.md` 是本仓库创作与评审用的中间格式：一个带 YAML frontmatter 的 Markdown 文件，人能直接读、diff 干净、评分器能逐段检测。发布时经 `tools/card_md_to_st.py` 转换为 `chara_card_v2` JSON 与 v3 PNG 嵌卡，导入 SillyTavern、Foreverse 或任何兼容前端。

Why not author JSON directly? Because character cards are prose. A 2,000-character opening message inside a JSON string literal is unreviewable — no meaningful diffs, no inline comments, escaped newlines everywhere. Markdown keeps the writing readable and the scorer (`tools/score_card.py`) can address each section by heading.

## 1. 文件骨架

```markdown
---
name: 角色名
scenario: 一句话：谁在哪、正发生什么、user 为什么在场
system_prompt: 扮演规则（单行长文本，或缩进续行）
tags: [限左, 都市, 洁, 难攻略, 年上]
image_prompt: 封面生图提示词（可选）
creator: 作者署名（可选，默认 Foreverse Team）
character_version: "1.0"（可选）
---
## Description
（人设八股）

## Personality
（场景化性格 + 语言指纹）

## Scenario
（当下冲突现场；frontmatter 的 scenario 优先）

## First Message
（开场白，可含 HTML 状态条）

## Alternate Greeting 1
（备选开场白；可有多条：Alternate Greeting 2、3…）

## Example Dialogue
（示例对话，{{user}} / {{char}} 轮次）

## Lorebook
### 条目名 | keys: 触发词1, 触发词2 | order: 250
条目正文……

### 常开条目名 | constant | order: 10
条目正文……

## Creator Notes
（作者的话）
```

## 2. Frontmatter 字段

| 字段 | 必填 | 映射到 chara_card_v2 |
| --- | --- | --- |
| `name` | 是 | `data.name` |
| `scenario` | 建议 | `data.scenario`（缺省回落 `## Scenario` 章节） |
| `system_prompt` | 建议 | `data.system_prompt` |
| `tags` | 是 | `data.tags`（`[a, b]` 数组或逗号分隔均可） |
| `image_prompt` | 否 | 不进卡 JSON，仅供生图管线 |
| `creator` | 否 | `data.creator` |
| `character_version` | 否 | `data.character_version` |

Frontmatter 里以 `#` 开头的行是注释（创作定位等内部记录），转换器忽略、不进产物。

## 3. 章节语义

- 章节名大小写不敏感；`## Alternate Greeting N` 可出现多次，按序进 `data.alternate_greetings`。
- `## First Message` → `data.first_mes`；`## Example Dialogue` → `data.mes_example`。
- 未列出的自定义章节会被转换器忽略（可以放创作笔记，但建议内部记录别进公开卡文件）。

## 4. Lorebook 条目头语法

每条一个三级标题，`|` 分隔的键值对声明触发与插入行为：

```
### 条目名 | keys: a, b | constant | order: 250 | depth: 3 | position: before_char
        | secondary: c, d | logic: and_any | prob: 25 | sticky: 2 | cooldown: 6
        | recursion: prevent | group: 组名 | weight: 150
```

| 键 | 语义 | 映射 |
| --- | --- | --- |
| `keys:` | 主触发词（逗号分隔）；**缺省即视为常开**（转换器自动置 `constant=true`，防产出永不触发的死条目） | `entry.keys` |
| `constant` | 常开注入 | `entry.constant = true` |
| `order:` | 插入排序（大者离生成点近） | `entry.insertion_order` |
| `position:` | `before_char` / `after_char`（默认 before_char） | `entry.position` |
| `depth:` | @depth 注入深度 | `entry.extensions.depth`（并置 position=at_depth） |
| `secondary:` + `logic:` | 副触发词与逻辑（`and_any/and_all/not_any/not_all`） | `entry.secondary_keys` + `extensions.selectiveLogic`（映射为 ST 数字枚举 0/3/2/1） |
| `prob:` | 0–100 触发概率 | `extensions.useProbability/probability` |
| `sticky:` / `cooldown:` | 命中后持续 / 冷却（消息数） | `extensions.sticky/cooldown` |
| `recursion:` | `exclude`（不被连锁触发）/ `prevent`（不再连锁下层） | `extensions.exclude_recursion / prevent_recursion` |
| `group:` + `weight:` | 同组互斥抽一 + 权重 | `extensions.group / group_weight` |

注：`prob/sticky/cooldown/group` 等高级 meta 走 SillyTavern 的 world-info extensions 字段；不支持这些扩展的前端会按普通条目处理（fail-open）。

## 5. 转换与校验

```bash
# 评分（八维 + 反 AI 味，<75 分退出码 1）
python3 tools/score_card.py cards/zh/songzhixia/card.md

# 转换：v2 JSON + v3 PNG 嵌卡（PNG 同时写入 `chara` 与 `ccv3` 两个 tEXt 块）
python3 tools/card_md_to_st.py cards/zh/songzhixia/card.md \
    --avatar cards/zh/songzhixia/cover.png --outdir out/ --slug songzhixia
```

导入方式：SillyTavern「导入角色」直接选生成的 PNG 或 `.v2.json`；Foreverse 在角色页导入同一份文件。
