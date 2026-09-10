"""BA1 字段补齐批测试（迁移 f2a3b4c5d6e7）

覆盖：
- 迁移后 6 个新字段存在性（alembic upgrade head 可在本机测试库幂等执行）
- save 系接口 remark 落库可回读（ContentOut 出参）
- voice duration 从 extra.duration_ms 换算秒落库
- MessageOut 出参含 content_id
- stats/daily-summary 出参含 total_bytes 字节聚合

前置：本地 PostgreSQL yishu 库 + Redis（对齐 tests/test_contents.py 前置）
"""
import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """登录拿 token（微信 mock）"""
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"ba1-{uuid.uuid4().hex[:8]}", "device_id": "ba1-dev"},
    )
    assert r.status_code == 200, r.text
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 迁移字段存在性
# ---------------------------------------------------------------------------


def test_migration_upgrade_head_and_columns_exist():
    """alembic upgrade head 在本机测试库幂等执行 + 6 个新列存在（验收#2）"""
    from alembic import command
    from alembic.config import Config
    from app.db.session import SessionLocal
    from sqlalchemy import text

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")  # 已在 head → 幂等无操作

    db = SessionLocal()
    try:
        rows = db.execute(
            text(
                "SELECT table_name, column_name, data_type FROM information_schema.columns "
                "WHERE (table_name='contents' AND column_name IN "
                "('duration','remark','size_bytes','tags_json','ai_description')) "
                "OR (table_name='messages' AND column_name='content_id') "
                "ORDER BY table_name, column_name"
            )
        ).all()
        got = {(t, c): d for t, c, d in rows}
        assert ("contents", "duration") in got, f"contents.duration 缺失: {rows}"
        assert ("contents", "remark") in got, f"contents.remark 缺失: {rows}"
        assert ("contents", "size_bytes") in got, f"contents.size_bytes 缺失: {rows}"
        assert ("contents", "tags_json") in got, f"contents.tags_json 缺失: {rows}"
        assert ("contents", "ai_description") in got, f"contents.ai_description 缺失: {rows}"
        assert ("messages", "content_id") in got, f"messages.content_id 缺失: {rows}"
        # 类型核对（迁移规格：int/bigint/jsonb/text/uuid）
        assert got[("contents", "duration")] == "integer"
        assert got[("contents", "size_bytes")] == "bigint"
        assert got[("contents", "tags_json")] == "jsonb"
        assert got[("messages", "content_id")] == "uuid"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# save 系接口 remark / duration 落库可回读
# ---------------------------------------------------------------------------


def test_save_with_remark_roundtrip(client, auth_headers, cleanup_user):
    """POST /contents 带 remark → 列表回读 remark + 新出参字段不缺（验收#4）"""
    r = client.post(
        "/api/v1/contents",
        json={
            "content_type": "text",
            "text": "BA1 备注回读用例",
            "source": "app",
            "remark": "这是用户备注",
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    content_id = data["id"]
    assert data["remark"] == "这是用户备注"
    # 全可空新字段在出参上存在（老数据语义：None 合法）
    assert "duration" in data and "size_bytes" in data
    assert "tags_json" in data and "ai_description" in data

    # 列表端点按 content_id 精确取回读
    r2 = client.get(
        "/api/v1/contents", params={"content_id": content_id}, headers=auth_headers
    )
    assert r2.status_code == 200, r2.text
    items = r2.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["remark"] == "这是用户备注"

    cleanup_user(_db_for_cleanup(), _current_user_id(auth_headers))


def test_voice_duration_from_extra_ms(client, auth_headers, cleanup_user):
    """voice 走 POST /contents 时 extra.duration_ms → duration 秒换算（BA1 落库点②）"""
    r = client.post(
        "/api/v1/contents",
        json={
            "content_type": "voice",
            "text": "BA1 语音时长用例",
            "source": "app",
            "extra": {"duration_ms": 6500},
        },
        headers=auth_headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["duration"] == 6  # 6500ms // 1000 = 6s（向下取整）

    cleanup_user(_db_for_cleanup(), _current_user_id(auth_headers))


# ---------------------------------------------------------------------------
# MessageOut.content_id
# ---------------------------------------------------------------------------


def test_message_out_contains_content_id(client, auth_headers, cleanup_user):
    """notify.create_message 落 content_id 列 → GET /messages 出参直出（验收#4）"""
    user_id = _current_user_id(auth_headers)
    from app.db.session import SessionLocal
    from app.services.notify import create_message

    db = SessionLocal()
    try:
        content_id = str(uuid.uuid4())
        create_message(
            db,
            user_id,
            channel="push",
            msg_type="voice_done",
            title="BA1 测试消息",
            body="消息出参 content_id 用例",
            payload={"content_id": content_id, "template": "mock"},
        )
        db.commit()
    finally:
        db.close()

    r = client.get("/api/v1/messages", headers=auth_headers)
    assert r.status_code == 200, r.text
    items = r.json()["data"]["items"]
    assert items, "消息列表为空（create_message 未落库？）"
    target = next(m for m in items if m["title"] == "BA1 测试消息")
    assert target["content_id"] == content_id

    cleanup_user(_db_for_cleanup(), user_id)


# ---------------------------------------------------------------------------
# stats total_bytes
# ---------------------------------------------------------------------------


def test_stats_daily_summary_total_bytes(client, auth_headers, cleanup_user):
    """stats/daily-summary 出参含 total_bytes = 未删内容 size_bytes 求和（验收#4）"""
    user_id = _current_user_id(auth_headers)
    from app.db.session import SessionLocal
    from sqlalchemy import text as sa_text

    db = SessionLocal()
    try:
        for size in (100, 250):
            db.execute(
                sa_text(
                    "INSERT INTO contents (id, user_id, content_type, status, created_at, updated_at, size_bytes) "
                    "VALUES (:cid, :uid, 'text', 'done', now(), now(), :size)"
                ),
                {"cid": str(uuid.uuid4()), "uid": user_id, "size": size},
            )
        db.commit()
    finally:
        db.close()

    r = client.get("/api/v1/stats/daily-summary", headers=auth_headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert "total_bytes" in data, f"stats 出参缺 total_bytes: {data.keys()}"
    assert data["total_bytes"] == 350  # 100 + 250（NULL 不参与 SUM）

    cleanup_user(_db_for_cleanup(), user_id)


# ---------------------------------------------------------------------------
# 工具
# ---------------------------------------------------------------------------


def _current_user_id(headers) -> str:
    """从 access token 解析当前用户 id"""
    from app.core.security import decode_token

    token = headers["Authorization"].split(" ", 1)[1]
    return decode_token(token)["sub"]


def _db_for_cleanup():
    """清理用独立会话（用例内已关闭原会话）"""
    from app.db.session import SessionLocal

    return SessionLocal()
