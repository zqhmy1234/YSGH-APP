"""PG 集成测试（验证当前配置的数据库连通与 ORM 表可访问）

前置：DATABASE_URL 指向已通过 Alembic 初始化的测试数据库。
运行：pytest backend/tests/test_db_integration.py -v
"""


import pytest
from app.core.config import settings
from app.db.session import engine
from sqlalchemy import text
from sqlalchemy.engine import make_url


@pytest.mark.integration
def test_db_connect():
    """连接到 DATABASE_URL 指定的库和角色。"""
    configured = make_url(settings.database_url)
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_database(), current_user"))
        db, user = result.one()
        assert db == configured.database
        assert user == configured.username


@pytest.mark.integration
def test_schema_tables_present():
    """ORM 受管表已落地（P2-05 Alembic 权威后：以 ORM metadata 为准，
    不再断言 schema.sql 遗留空表——遗留表已被 baseline 收敛清理）"""
    from app.db.session import Base as _Base

    required = sorted(_Base.metadata.tables.keys())
    with engine.connect() as conn:
        rows = conn.execute(
            text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'")
        ).fetchall()
        tables = {r[0] for r in rows}
    missing = [t for t in required if t not in tables]
    assert not missing, f"缺表: {missing}"


@pytest.mark.integration
def test_soft_delete_columns():
    """软删除全局约定：业务表带 deleted_at"""
    with engine.connect() as conn:
        cols = conn.execute(
            text("""
                SELECT table_name, column_name FROM information_schema.columns
                WHERE table_schema='public' AND column_name='deleted_at'
            """)
        ).fetchall()
    tables_with_deleted = {r[0] for r in cols}
    # 核心业务表必须带软删除（B4-2）
    for t in ["users", "contents", "events"]:
        assert t in tables_with_deleted, f"{t} 缺 deleted_at"


@pytest.mark.integration
def test_vector_extension():
    """pgvector 扩展可用（v3 后无向量列，预留供未来纠错向量/同步使用）"""
    with engine.connect() as conn:
        row = conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).fetchone()
        assert row is not None
