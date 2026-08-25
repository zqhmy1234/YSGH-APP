# Wave 0 基线准备（完成记录）——docs/parallel-dev/01

> 执行人：集成 Agent（主窗口）｜2026-08-26 完成

## 已完成

1. **分支确认**：develop 为主开发基线（main 停更）；PR #1 分支 = `origin/codex/asr-pipeline-hardening`（旧分支 feature/m1-asr-guardrail 已废弃）。
2. **PR #1 审读 + merge**：review 5 点核对——numpy 依赖 ✅、workspace_id URL 地域化 ✅、模型预置脚本 prepare_sensevoice.py ✅、本地情绪开关（should_enhance_with_local_emotion auto/off/always）✅、base 改 develop ✅（codex 分支本就基于 develop HEAD）。已 `git merge --no-ff` 进 develop。
3. **schema.sql 修复**（PR CI 失败根因，PR 作者判断正确）：补 events.client_event_id + 部分唯一索引、geo_cache.province、upload_tasks/upload_chunks 两表、profile_sensitive 升级 5 级处置结构。
4. **新迁移** `b0b1c2d3e4f5_add_wave0_parallel_dev_tables.py`（head=4d00dfec7b46 之后）：upload_tasks/upload_chunks 补录 + profile_sensitive/sensitive_words 重建 + profile_annotation_pool 新增。
5. **models.py** 新增 ORM：ProfileSensitive（disposition 5 级+evidence）、SensitiveWord（level 1/2/3）、ProfileAnnotationPool（低置信度池）。
6. **pipeline_ext/ 钩子包**：__init__（extend_payload/mark_sensitive_on_ingest/annotate_on_ingest/consume_emotion）+ payload.py/sensitive.py/profile.py/emotion.py（no-op stub，各域 Agent 填 TODO）。
7. **pipeline.py 埋钩子**（4 处调用点，try/except 包裹，失败不阻塞主流程）：_index_content payload 扩展、_process_text、_process_voice、_process_photo。
8. **llm_ops/ 聚合包**：base.py（转发 dashscope：chat_text/moderate/rewrite_query/route_query）+ rerank.py/event_merge.py/annotate.py/guard.py（stub）。
9. **测试基线**：merge 后 test_sync/test_upload/test_asr/test_event_sync 57 passed；test_pipeline 本地失败因 Qdrant/Docker 未启动（环境，CI 验证）；test_upload 3 失败为本地环境（STORAGE_BACKEND=fs 覆盖 + 无 Redis）。
10. **B5a/B5d 修正后待办**：见 `_B5a_B5d修正后待办.md`（原 audit 约 2/3 项被 PR #1 解决）。

## 遗留（集成 Agent 后续处理）

1. CI 的 Qdrant 依赖确认：ci.yml services 只有 postgres/redis，test_pipeline/test_rag 在 CI 的向量库来源需确认（可能需加 qdrant service 或依赖本地安装）。
2. PR #1 未验证项：FunASR 主通道未来返回情绪时 auto 模式跳过本地（J-8 登记）。
3. 本地环境：Redis 未装（涉及 RQ 测试本地红）；Docker Desktop 未启动（Qdrant 不可用）。
