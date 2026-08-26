# Wave 3 · Agent I（B1 画像域）任务卡——docs/parallel-dev/09

> ✅ 已完成并集成（2026-08-26，merge 1f958fe + CASCADE 迁移 f85a393）

## Mission
完成 B1 画像域 6 项：LLM 枚举标注管线、冷启动 L1 兴趣稀疏激活、更新规则（强度累加/替换进历史/节流）、证据锚点+新鲜度戳、低置信度池消费、枚举集 JSON 接线+收尾。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B1_profile.md`——#1 标注管线未实现（interview.py:6-9 自述待 key）、#2 兴趣稀疏未激活、#12 更新规则为并集写入（interview.py:148）、#9 证据锚点（profile_l2_evidence 空表）、#3 低置信度池（Wave 0 已建 profile_annotation_pool）、#15 枚举集零加载、#14 L0 13 维收尾。
3. 现状：冷启动三问已闭环（interview.py）；`docs/画像维度枚举集_l0.json`（51 维，38/51 有 values_detail）、`画像维度枚举集_l1_骨架.json`（193 维全 refined）；`pipeline_ext/profile.py`（你的入库标注钩子）、`llm_ops/annotate.py`（你的 LLM 映射）。

## Scope（可改）
1. `backend/app/services/interview.py`、`backend/app/api/interview.py`、`backend/app/schemas/interview.py`
2. `backend/app/services/pipeline_ext/profile.py`（**你的钩子**：annotate_on_ingest）
3. `backend/app/services/llm_ops/annotate.py`（**你的 LLM 映射**：种子匹配→同义归一→新增 value）
4. `docs/画像维度枚举集_l0.json`、`docs/画像维度枚举集_l1_骨架.json`（收尾：L0 13 维 values_detail、sensitive_topic "??" 占位修正、L1 补顶层 phrase+disclosure）
5. **新文件** `backend/app/services/profile_annotator.py`（标注核心：别名表/查重/历史写入/节流）、`backend/app/services/profile_schema.py`（枚举集 JSON 加载器）
6. `backend/tests/test_interview.py`、新建 `test_profile_annotator.py`

## 绝不碰（只读）
models.py/migrations/（profile 表已齐：user_profile/dimension_history/dimension_pending/annotation_pool/l2_evidence 表存在）；pipeline.py（经 pipeline_ext）；dashscope.py（经 llm_ops/base.chat_text）；client/（interview.uvue 已交付，无改动需求）；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **枚举集 JSON 加载器**：profile_schema.py 加载 l0/l1 JSON → 内存枚举集（维度/枚举值/别名/阈值/披露层）；零硬编码。
2. **LLM 枚举标注**：llm_ops/annotate.annotate(text) → [{dimension, enum_value, confidence}]；prompt 约束"只映射不生成"，输出 JSON。
3. **标注核心**：profile_annotator.py——置信度双门槛（普通≥0.7/超细性格≥0.8）；<0.7 → profile_annotation_pool；开放枚举（同义归一→别名表→直接新增 value 带证据+时间戳+查重）；更新规则（同值强度累加/异值替换+旧值进 history 最近 10 条/同日同维度节流）；证据锚点写 profile_l2_evidence。
4. **钩子接线**：pipeline_ext/profile.py 调 annotator（文本/语音/照片 caption 入库即标注）。
5. **冷启动兴趣稀疏**：interview.py 三问后补 L1 兴趣稀疏激活（5-10 维，规则+LLM 候选）。
6. **枚举集收尾**：L0 13 维补 values_detail（confidence_trace/chat_history_recent 连 phrase 都缺）；sensitive_topic note "??" 修正；L1 补顶层 phrase + disclosure 字段。

## Dependencies
- DASHSCOPE key（真实标注；无 key 用 mock 通道 + 标注待验证）
- Wave 0 表（annotation_pool/l2_evidence 已建）

## DoD
0. **门禁（2026-08-26 快/全量拆分新规）**：commit 时 pre-commit 自动跑快速门禁（秒级）；**完成声明前必须跑 `python scripts/review_agent.py --full` 全绿**（仓库级 + 全量测试，集成 Agent 与 CI 同口径验收）。
1. 新测试全过（annotator：阈值/池/节流/查重/历史裁剪）。
2. 枚举集 JSON 校验通过（补全后自检：维度数/枚举值/引用完整性）。
3. 更新 .cowork-temp/audit_B1_profile.md 状态列。
4. 完成消息：文件清单 + 测试 + 待 key 项。

## Integration
分支 `wave3-agentI`；与 G/H 并行（文件域零重叠）；merge 后全量测试 + 契约更新（如加标注状态端点）。
