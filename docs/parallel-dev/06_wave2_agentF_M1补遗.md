# Wave 2 · Agent F（M1 补遗域）任务卡——docs/parallel-dev/06

## Mission
完成 M1 补遗 6 项：第二层 LLM 精排（llm_ops.rerank + rag.py 接线）、reranker 默认开策略、百炼托管护栏接入、50 条真实评测集+三指标、评测体系 5 项落地、纠错 7 天测量脚本。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B2_rag.md`——#1 第二层精排未实现（feature_list 自认）、#2 rerank_enabled=False（config.py:101-104，CPU 40s 超门禁）、#7 三指标零实现、#8 评测体系 5 项、#13 LLM 改写真实验证；`audit_B5b_B5c_B5e.md`——B5b #1 托管护栏未接（dashscope.py:25 注释）、B5c #5 测量脚本。
3. 现状：`llm_ops/rerank.py`（stub，你的入口）；`research/rag_benchmark/`（metrics.py 只有检索层指标）；`scripts/reflow_global.py`（纠错流水线已真实）；`docs/RAG评测体系与门禁标准.md` §6 列 5 项待落地。

## Scope（可改）
1. `backend/app/services/llm_ops/rerank.py`（**你的 LLM 精排**）
2. `backend/app/services/rerank.py`（一层 bge 粗排，只读现状，可加开关策略）
3. `backend/app/services/rag.py`（**跨波次**：Wave 1 Agent A 改过，你基于 merge 后 develop 开发；只加精排接线点，不动 A 的过滤/payload 逻辑）
4. `research/rag_benchmark/`（metrics.py 加 faithfulness/relevancy/context precision；queries 采集 50 条真实查询）
5. `backend/tests/test_rag.py`（精排相关新增）、新建 `test_eval_suite.py`、`scripts/measure_correction_gain.py`（新建，纠错测量）
6. `backend/app/core/config.py`（rerank_enabled 默认策略——只读现有值，策略注释说明，GPU 检测自动开）

## 绝不碰（只读）
dashscope.py（托管护栏经 llm_ops/base 登记需求或由集成 Agent 处理——**注意：百炼托管护栏（qwen_response_check header）需要改 dashscope.py 的 moderate，dashscope 冻结** → 你在 llm_ops/guard.py 或新文件 `llm_ops/guard_managed.py` 实现托管调用（httpx 直发 header），不动 dashscope.py）；pipeline.py；models.py/migrations/；client/；feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **第二层 LLM 精排**：llm_ops/rerank.llm_rerank(query, hits)——qwen-flash 判断"这段能否回答该问题"，输出 top-5 + 理由；rag.py 接线（bge 粗排 top-50→top-10 → LLM 精排 → top-5）；无 key/mock 时原序返回。
2. **reranker 默认开策略**：config 增加 `rerank_auto_enable`（检测 GPU/模型就绪自动开，CPU 保持关）；文档化门禁（P95<3s 前提下开）。
3. **百炼托管护栏**：llm_ops/guard_managed.py 实现 `qwen_response_check`（X-DashScope-DataInspection header，httpx 直发，不碰 dashscope.py）；接线为 moderate 的"托管优先、chat 兜底"策略（在 llm_ops/base 或调用方选择）。
4. **50 条真实评测集**：采集/合成 50 条贴近真实使用的中文查询（描述性/关键词/时间/地点/人物/图片 7 层分布），落 research/rag_benchmark/truth_queries_50.json + 评测跑批。
5. **三指标实现**：metrics.py 增加 faithfulness（答案引用原文比例）、relevancy（答案与查询相关）、context precision（上下文排序质量）；跑出基线报告。
6. **评测体系 5 项**：run_eval --truth-a、B/C/D 语料 ingestion（build_truth_corpus.py）、expect_empty 负样本钩子、Query 改写层正确率评测、跨模态 route 层评测。
7. **纠错测量脚本**：scripts/measure_correction_gain.py（7 天同类准确率提升≥10% 测量；口径待产品部，脚本先按"全量重测 vs 抽样"双模式）。

## Dependencies
- DASHSCOPE key（精排/托管护栏/评测真实调用；无 key 代码先行 + mock）
- Qdrant（评测跑批；本地不可用标 CI）
- Wave 1 Agent A merge 后的 rag.py（跨波次，先等 Wave 1 集成完成再开工本项 1/2）

## DoD
1. 精排与护栏测试通过（mock 模式）；评测脚本可跑（环境依赖标注）。
2. 更新 .cowork-temp/audit_B2_rag.md 与 audit_B5b_B5c_B5e.md 状态列。
3. 完成消息：文件清单 + 测试 + 指标基线 + 待 key 项。

## Integration
分支 `wave2-agentF`；与 D/E 并行（同包不同文件安全）；rag.py 跨波次（Wave 1 A 后）；merge 后全量测试（含 -m rag 14 项 CI 验证）+ 契约更新。
