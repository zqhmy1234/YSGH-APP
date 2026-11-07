-- 忆述光华 · PostgreSQL 隔离建库脚本
-- 用途：为忆述光华创建独立数据库 + 专用角色，不影响本机其他项目的数据库
-- 用法：
--   $env:PGPASSWORD="admin"
--   psql -U postgres -h localhost -f setup_pg.sql
-- 验证：
--   psql -U yishu_app -h localhost -d yishu -c "select 1;"

-- 1. 专用角色（非超级用户，权限最小化）
DO $$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'yishu_app') THEN
        CREATE ROLE yishu_app LOGIN PASSWORD 'yishu_app_2026';
    END IF;
END
$$;

-- 2. 独立数据库（owner = yishu_app，与现有库完全隔离）
SELECT 'CREATE DATABASE yishu OWNER yishu_app'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'yishu')
\gexec

-- 3. 只给角色自己库的权限（避免影响其他库）
GRANT ALL PRIVILEGES ON DATABASE yishu TO yishu_app;
REVOKE ALL ON DATABASE postgres FROM yishu_app;

-- 4. 连接隔离说明（应用侧）：
--    DATABASE_URL=postgresql+psycopg://yishu_app:yishu_app_2026@localhost:5432/yishu
--    schema 迁移时在 yishu 库内执行 backend/sql/schema.sql

-- 5. pgvector 扩展（非 trusted，需超级用户安装；schema.sql 里 IF NOT EXISTS 幂等跳过）
--    2026-08-26：本地新库 setup 与 CI 一致，避免 yishu_app 跑 schema.sql 时权限失败
\connect yishu

CREATE EXTENSION IF NOT EXISTS vector;
