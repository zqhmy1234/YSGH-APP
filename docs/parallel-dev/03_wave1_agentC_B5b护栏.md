# Wave 1 · Agent C（B5b 护栏域）任务卡——docs/parallel-dev/03

## Mission
完成 B5b 护栏域 6 项：FIX-4 profile_sensitive 重建接线、事件级敏感标签分类器（sensitive_tags 写入）、敏感有效期、违规词回流、检测器接口抽象、搜索规则级过滤。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B5b_B5c_B5e.md`（.cowork-temp/）——B5b 部分 #1-#9。
3. Wave 0 已建：`models.ProfileSensitive`（5 级 disposition）、`models.SensitiveWord`（level 1/2/3）、迁移已重建两表；`pipeline_ext/sensitive.py`（你的钩子）、`llm_ops/guard.py`（你的 LLM 补漏）。
4. 现状：回响敏感双查只做了"检测"半边（echo.py:44-53），画像 L1 校验待接线（echo.py:42-43 注释 P1-06）；contents.sensitive_tags 零写入；敏感有效期/违规回流零代码。

## Scope（可改）
1. `backend/app/services/echo.py`、`backend/app/services/external/sensitive_words.py`
2. `backend/app/services/pipeline_ext/sensitive.py`（**你的钩子**：mark_sensitive_on_ingest 实现）
3. `backend/app/services/llm_ops/guard.py`（**你的 LLM 补漏**）
4. `backend/app/api/contents.py`（仅敏感相关分支，勿动上传/管线逻辑）、`backend/app/schemas/content.py`
5. `backend/data/sensitive/`（词表维护，只加不改删）
6. `backend/tests/test_echo.py`、`test_sensitive_words.py`、新建 `test_event_sensitive.py`

## 绝不碰（只读）
pipeline.py、dashscope.py（经 llm_ops/base.moderate 调用）、models.py、migrations/、rag.py（搜索过滤如需改 → 在完成消息登记，由集成 Agent 转 Agent A）、client/、feature_list.json、progress.md、docs/parallel-dev/（只读）。

## TODO 清单
1. **FIX-4 profile_sensitive 接线**：echo.py 双查补"画像 L1 校验"（查 ProfileSensitive，命中 disposition∈{forbid,cautious,review} → 跳过不重提）；提供对话式增删接口（B1-6 需要，`/profile/sensitive` 增删查，模型已就绪）。
2. **事件级敏感分类器**：mark_sensitive_on_ingest——规则词表先行（复用 sensitive_words.py 词表 + 5-8 类映射）→ 规则未命中走 llm_ops/guard.detect_event_sensitive（qwen-flash 补漏）→ 命中写 contents.sensitive_tags + sensitive_status；置信度/来源记录。
3. **敏感有效期**：事件级带时间戳 + 最近提及计数（用户主动提及 +1），≥3 次降级普通话题；画像级永不过期（locked）。
4. **违规词回流**：检测违规（moderate 命中或 LLM 判敏感且规则未覆盖）→ 写 SensitiveWord(level=3)，自动加入本地规则表。
5. **检测器接口抽象**：定义规则/百炼托管/自部署三实现接口（轻量抽象基类，不引入重框架）；当前接线规则+llm_ops.guard。
6. **搜索/摘要规则级敏感过滤**：摘要/搜索路径显式过敏感词表（设计 B5b-1 🟢 规则级）；登记给 Agent A 的搜索过滤需求时注明"规则级即可，不过模型"。

## Dependencies
- Wave 0 表/ORM（已就绪）
- llm_ops/base.moderate（已就绪，只读）
- 产品部口径：事件级敏感 5-8 类清单（设计已列：分手/离世/健康/金钱/家庭矛盾，先用设计清单）

## DoD
1. 新增测试全过（test_event_sensitive.py 等）；本地可跑（不依赖 Qdrant）。
2. echo 双查完整（画像 + 检测）。
3. 更新 .cowork-temp/audit_B5b_B5c_B5e.md 状态列。
4. 完成消息：文件清单 + 测试 + 需集成 Agent 转交项（搜索过滤→Agent A）。

## Integration
分支 `wave1-agentC`；与 Agent A 并行（不同文件域）；merge 后跑全量测试；新增 API 需集成 Agent 重导出 OpenAPI。
