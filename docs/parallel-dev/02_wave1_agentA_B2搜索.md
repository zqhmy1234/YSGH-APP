# Wave 1 · Agent A（B2 搜索域）任务卡——docs/parallel-dev/02

## Mission
完成 B2 搜索域 8 项：修复 content_type 不一致（FIX-1 高危）、生产 payload 补 place/tags、时间词表扩充、溯源事件级归因、PG 降级兜底、corpus-A 补齐、LLM 改写真实验证、以图搜图延迟优化（部分）。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B2_rag.md`（.cowork-temp/）——尤其 #5 content_type（rag.py:285/427 用 "image"，pipeline 生产 payload "photo"）、#11 payload 缺 place/tags、#17 时间词表。
3. 关键文件现状：`backend/app/services/rag.py`、`vector_store.py`、`ner.py`、`backend/app/api/search.py`、`backend/app/services/pipeline_ext/payload.py`（已建 stub，你的钩子入口）。
4. 门禁数字：文字 hit_rate@3=0.9091 PASS、P95<3s PASS；以图搜图 P95=7629ms FAIL（image_search_report.json）。

## Scope（可改）
1. `backend/app/services/rag.py`、`vector_store.py`、`ner.py`、`rerank.py`（只读一层配置，默认关勿动）
2. `backend/app/api/search.py`、`backend/app/schemas/search.py`
3. `backend/app/services/pipeline_ext/payload.py`（**你的钩子文件**：extend_payload 实现 place/tags 同步 + content_type 归一）
4. `research/rag_benchmark/`、`scripts/eval_image_search.py`、`scripts/build_image_index.py`
5. `backend/tests/test_rag.py`、`test_search.py`、`test_image_search.py`、`test_ner.py`、`test_vector_store.py`

## 绝不碰（只读）
pipeline.py、dashscope.py、models.py、migrations/、feature_list.json、progress.md、session-handoff.md、AGENTS.md、docs/parallel-dev/（只读）、OpenAPI 契约（只读）；client/ 全部；llm_ops/（只读，除你无新需求）。

## TODO 清单
1. **FIX-1 content_type 归一**（最高优先）：统一 photo 语义——检索过滤与 payload 一致（建议 payload 用 "photo"，过滤端兼容 "image" 或统一常量）；补回归测试：photo 点可被图片意图命中。注意 `pipeline.py:upsert_image_vec` 也写 "photo"（只读，勿改文件，归一逻辑放 payload.py 钩子 + rag.py 过滤端）。
2. **payload 补 place/tags**：extend_payload 把 content.place 与 extra.ci_tags 同步进 Qdrant payload（NER place/tag 过滤真正生效，不再只靠空结果回退）。
3. **时间词表扩充**：rag.py `_TIME_PATTERNS` 增加"去年夏天/上上周/三年前/前年"等相对时间。
4. **溯源事件级归因**：rag.py:266-267 event_id/event_title 回填（事件表关联，B3 事件已落库）。
5. **Qdrant 降级 PG 兜底**：降级时改走 PG 全文检索（tsvector/ILIKE），不再空结果；degraded 标记保留。
6. **corpus-A 补齐**：61 张截图 caption 补索引（build_image_index.py，网络失败重试）。
7. **LLM 改写真实验证**：llm_rewrite_enabled=True 路径用 DASHSCOPE key 实测（拿 key 后），无 key 则标注待验证。
8. **以图搜图 P95 优化**：评估 caption 缓存/并发/降级开关；若需换 Qwen3-VL-Embedding（tongyi-embedding-vision-plus）→ 属于 B2-4，需 key，登记需求不阻塞。

## Dependencies
- llm_ops/base.py（Wave 0 已建，只读使用）
- DASHSCOPE key（项 7 需要；无 key 代码先行）
- Qdrant 本地不可用 → RAG 测试本地红，标"CI 验证"（CI 需确认 qdrant service）

## DoD
1. FIX-1 有回归测试且通过（本地可跑纯逻辑部分；Qdrant 依赖标 CI）。
2. 其余各项完成或明确标注"待 key/待环境"。
3. 更新 .cowork-temp/audit_B2_rag.md 状态列（完成项打勾）。
4. 完成消息：文件清单 + 测试结果 + 表需求（如有）+ 待 key 项。

## Integration
分支 `wave1-agentA`；合并顺序：Agent A 与 Agent C 并行（不同文件域），先 A 后 C 或反之均可；merge 后跑全量测试 + 重导出 OpenAPI（search 契约变更时）。
