# 忆述光华 · PostgreSQL 隔离建库脚本
# 用途：为忆述光华创建独立数据库 + 专用角色，**不影响本机其他项目的数据库**
# 用法：先设置 PGPASSWORD 环境变量，再运行：
#   $env:PGPASSWORD="<postgres超级用户密码>"
#   & "C:\Program Files\PostgreSQL\17\bin\psql.exe" -U postgres -h localhost -f scripts\setup_pg.sql
# 或执行 exec 里对应命令（见下方注释块）

-- 1. 专用角色（非超级用户，权限最小化）
-- 密码请修改为强密码；仅用于忆述光华应用连接
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
