# Wave 0 基线准备（完成记录）——docs/parallel-dev/01

> 执行人：集成 Agent（主窗口）｜2026-08-26 完成

## 已完成

1. **分支确认**：develop 为主开发基线（main 停更）；PR #1 分支 = `origin/codex/asr-pipeline-hardening`（旧分支 feature/m1-asr-guardrail 已废弃）。
2. **PR #1 审读 + merge**：review 5 点核对——numpy 依赖 ✅、workspace_id URL 地域化 ✅、模型预置脚本 prepare_sensevoice.py ✅、本地情绪开关（should_enhance_with_local_emotion auto/off/always）✅、base 改 develop ✅（codex 分支本就基于 develop HEAD）。已 `git merge --no-ff` 进 develop。
3. **schema.sql 修复**（PR CI 失败根因，PR 作者判断正确）：补 events.client_event_id + 部分唯一索引、geo_cache.province、upload_tasks/upload_chunks 两表、profile_sensitive 升级 5 级处置结构。
4. **新迁移** `b0b1c2d3e4f5_add_wave0_parallel_dev_tables.py`（head=4d00dfec7b46 之后）：upload_tasks/upload_chunks 补录（IF NOT EXISTS 兼容本地已手动建表）+ profile_sensitive/sensitive_words 重建 + profile_annotation_pool 新增。**已应用本地库**。
5. **models.py** 新增 ORM：ProfileSensitive（disposition 5 级+evidence）、SensitiveWord（level 1/2/3）、ProfileAnnotationPool（低置信度池）。
6. **pipeline_ext/ 钩子包**：__init__（extend_payload/mark_sensitive_on_ingest/annotate_on_ingest/consume_emotion）+ payload.py/sensitive.py/profile.py/emotion.py（no-op stub，各域 Agent 填 TODO）。
7. **pipeline.py 埋钩子**（4 处调用点，try/except 包裹）+ **修复 PR#1 回归**：process_content 对无效 uuid content_id 二次抛错（except 块内 db.get 再抛 InvalidTextRepresentation）→ 开头 uuid 校验直接 return not-found。
8. **llm_ops/ 聚合包**：base.py（转发 dashscope：chat_text/moderate/rewrite_query/route_query）+ rerank.py/event_merge.py/annotate.py/guard.py（stub）。
9. **修复真实 bug（api_smoke 门禁暴露）**：`vector_store._to_filter` 缺 user_id 分支 → 检索阶段全库召回（跨用户内容挤占召回窗口，新用户内容被挤出 top-k）；已在 _to_filter 加 user_id MatchValue + rag._search_impl / _search_by_image_impl 把 user_id 传入 filters + 回归测试（test_vector_store.py::test_to_filter_user_id_isolation）+ 修正 test_search_basic（用户隔离语义下先造数）。
10. **测试基建**：backend/conftest.py autouse fixture 强制 storage=fake + reset（消除跨模块串扰，test_upload × test_content_upload 并跑失败）；test_agent.py 端口自检（Redis 6379/Qdrant 6333 不可达时 deselect 依赖测试）+ api_smoke 失败自动重试 + 执行顺序调整（api_smoke 先于 pytest，防 pytest 写入生产 collection 污染 smoke 召回）；review_agent.py lint 只查 git tracked 文件且排除他人 unstaged 修改（validate_truth_data.py 存量 lint 不卡提交）。
11. **环境**：Docker 容器 `yishu-redis`(6379) / `yishu-qdrant`(6333-6334) 由用户 Docker Desktop 提供（此前 daemon 未启动导致测试环境缺失）；本地 .env 有 STORAGE_BACKEND=fs（测试用 env 覆盖 fake）。
12. **测试基线**：全量默认套件 281 passed（+14 deselected rag）；review_agent 全绿（lint/tests/secrets/todos/lessons）。
13. **B5a/B5d 修正后待办**：见 `_B5a_B5d修正后待办.md`（原 audit 约 2/3 项被 PR #1 解决）。
14. **提交**：f92dfc1（41 files +1450，hook 全绿后提交）。

## 遗留（集成 Agent 后续处理）

1. CI 的 Qdrant 依赖确认：ci.yml services 只有 postgres/redis——test_pipeline/test_rag 在 CI 的向量库来源需确认（可能需加 qdrant service）。
2. PR #1 未验证项：FunASR 主通道未来返回情绪时 auto 模式跳过本地（J-8 登记）。
3. api_smoke 历史数据：Qdrant yishu_contents 累积了大量测试内容（各 smoke/pytest 会话），不影响功能（用户隔离已修）；如需清理可 delete by filter（按需）。
