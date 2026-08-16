"""PG 集成测试（验证 yishu 隔离库连通 + 28 表可访问）

前置：本地 PostgreSQL 已建 yishu 库（scripts/setup_pg.sql + backend/sql/schema.sql）
运行：pytest backend/tests/test_db_integration.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.db.session import engine
from sqlalchemy import text


@pytest.mark.integration
def test_db_connect():
    """连接隔离库 yishu"""
    with engine.connect() as conn:
        result = conn.execute(text("SELECT current_database(), current_user"))
        db, user = result.one()
        assert db == "yishu"
        assert user == "yishu_app"


@pytest.mark.integration
def test_schema_tables_present():
    """28 表 schema 已落地（关键表抽查）"""
    required = [
        "users", "user_wechat_bindings", "devices", "sms_codes", "audit_log",
        "contents", "content_tags", "voice_segments",
        "events", "event_items", "event_edit_log",
        "user_profile", "profile_sensitive",
        "correction_log", "sensitive_words",
        "question_templates", "guardrail_logs",
        "question_history", "echo_history",
        "sync_state", "offline_queue", "deleted_logs",
        "wechat_messages",
        "geo_cache", "ai_request_logs", "api_cost_stats", "finetune_jobs",
        "app_settings",
    ]
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
    """pgvector 扩展可用（correction_log.content_embedding 依赖）"""
    with engine.connect() as conn:
        row = conn.execute(text("SELECT extname FROM pg_extension WHERE extname='vector'")).fetchone()
        assert row is not None
