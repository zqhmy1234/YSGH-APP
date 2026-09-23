-- ============================================================================
-- PostgreSQL 首次初始化脚本（仅容器**首次**建库时执行）
--
-- ⚠️ 幂等边界：`docker-entrypoint-initdb.d/` 只在数据目录为空时执行一次。
--   已有数据的库改本文件无效；需要补扩展时手工执行：
--   docker compose -f deploy/docker-compose.yml exec -T postgres \
--     psql -U postgres -d yishu -c "CREATE EXTENSION IF NOT EXISTS vector;"
--
-- 为什么必须有这一行：
--   - backend/sql/schema.sql 里有 `CREATE EXTENSION vector`（供 CI 建库）；
--   - 但 `backend/migrations/`（alembic）中**零 `vector` 引用**（已核实）→ 生产走
--     `alembic upgrade head` 时不会有任何一处创建该扩展；
--   - 而后端测试存在「pgvector 扩展存在性」断言（requirements.txt:18-19 注释明载）。
--   故生产库的 vector 扩展由本脚本负责，缺它则向量能力相关链路与断言直接失败。
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;
