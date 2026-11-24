# 六个人物卡：写卡手册

这不是“填参数表”，是写一个能被人记住的人。六张卡必须能靠语气、关注点和示例对话分辨；如果遮住 `display_name` 还像同一个人，就没写完。

## 一张卡包含什么

| 字段 | 写什么 | 不写什么 |
|---|---|---|
| `role_id` | 稳定内部 id，六选一 | 不写中文名当 id |
| `display_name` | 她的名字（小忆/忆生/阿静/阿言/小树/阿册） | 不写“温柔助手”这类功能名 |
| `description` | 她是谁、她怎么待人、她身上有什么具体的习惯 | 不写“善良体贴”这种形容词清单 |
| `personality` | 她怎么想事：先注意什么、会怎么误解、什么时候退开 | 不写 0–1 数字，不写“外向 80%” |
| `scenario` | 她和这个人大概是什么关系、相处到什么程度 | 不替用户编造具体身世 |
| `first_message` | 她第一次会说的原话 | 不写“您好，我是您的 AI 助手” |
| `example_dialogue` | 2–3 段“对方说—她答”，能看出她怎么接话 | 不写教学式标准答案 |
| `creator_notes` | 作者手记：为什么这么写、哪句最像她 | 不进 prompt，只给人看 |
| `what_she_never_says` | 3–5 句完整的禁语，带语气 | 不写“不要不礼貌”这种空话 |
| `how_she_usually_responds` | 默认回应策略：先接哪半句、要不要问、什么时候停 | 不写“根据情况灵活处理” |
| `memory_boundaries` | 她能引用什么、不能编什么、拿不准时怎么说 | 不写“保护隐私”这种口号 |
| `lore` | 3–4 条生活设定，像真人的生活碎片 | 不写用户事实，不写剧情任务 |
| `taboo` | 机器安全子集，必须来自全局 `TABOO` | 不能自由扩写 |

## 七段式正文

`description` → `personality` → `scenario` → `first_message` → `example_dialogue` → `what_she_never_says` → `how_she_usually_responds` → `memory_boundaries`。前七段构成“她是谁/她怎么想/她和你/她第一句/她怎么接/她绝不说/她默认怎么做”，`lore` 是附录。

## 三条硬规矩

1. **用完整句子，不用标签。** 不写“温暖=高、毒舌=低”，写“她不会一上来就安慰你，她会先问你这句话是在什么情况下说的”。
2. **每个人至少有一处“不讨喜”的真实。** 小忆有时候会停顿太久，忆生会追问细节，阿静不擅长给建议，阿言会把事情讲得比原来长，小树会反复确认，阿册翻页前总要问一句。没有毛边的人不像人。
3. **禁语要像她会说的话。** “你就是难过”“别想太多”“这有什么好难过”是句子，不是关键词；它们同时受全局 `TABOO` 约束。

## 关系约定与回应说明不是卡的一部分

- 三组“谁先开口 / 一次说多少 / 建议放在哪”由用户在设置页选，后端拼成整段 `relationship_note`；**不写进卡**，否则会把某一种关系硬编码给所有人。
- 每一轮的“看见什么/需要什么/第一句/不能做什么/说多长/听见的原话/多确定”由 `ResponseBrief` 规则层生成；**不写进卡**，否则会把某一种情境当成所有对话。
- 卡的身份、禁忌、安全边界不被用户的三组选择覆盖。

## 导入/导出

- 只支持 Character Card V2 作为交换格式；`system_prompt` / `post_history_instructions` 导入即拒绝，`creator_notes` 保留但不进 prompt。
- 导入时把 `description/personality/scenario/first_mes/mes_example/creator_notes` 存进 `source_profile`，导出优先写回，避免“导入再导出就失真”。
- 新增卡后更新 `manifest.json`（`card_hash` 与 `version`），并跑：
  ```bash
  cd agent && .venv312/bin/python -m pytest tests/harness/test_role_card.py tests/prompts/test_role_card_assets.py -q
  ```
- 自检：把六张卡的 `render()` 并排读，遮住名字，仍能说出“这是谁”；说不出来就重写，不要加更多形容词。

## 安全边界

- 卡不能覆盖 `## global` 平台约束；`system_prompt` 永远不进上下文。
- 卡不能读 `may_read_face_result=false` 的层；`memory_boundaries` 只描述，不赋权。
- 卡不能软化 T2/T3 危机话术；危机在 Agent 外短路。
- 卡里的 `lore` 是设定，不是用户事实；回复引用时必须标明“设定参考”，不能写进记忆。
