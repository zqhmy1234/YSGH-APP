"""数据导出测试（US-42 · Wave1-B2 · 任务卡 v2 矩阵）

覆盖：空库 / 满库 / 跨用户越权隔离 / 每类上限截断(truncated) / 坏格式 422 /
无 token 401 / 限流 429。

注：export router 的 main.py 注册由集成 Agent 接线（v2 文件域：main.py 绝不碰），
故本测试用独立 FastAPI app 挂载 export_router + 覆写 get_current_user（get_db 用
真实 PG 会话，数据直接经 SessionLocal 落库）。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.api.deps import get_current_user
from app.api.export import router as export_router
from app.core.errors import install_error_handlers
from app.db.models import (
    Content,
    CorrectionLog,
    Event,
    EventItem,
    ProfileSensitive,
    User,
    UserProfile,
)
from app.db.session import SessionLocal
from app.services.export import _hash_user_id
from fastapi import FastAPI
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


@pytest.fixture()
def export_client():
    """独立 app（export router 未在 main.py 注册——集成 Agent 接线；测试自建挂载）"""
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(export_router)
    return TestClient(app)


@pytest.fixture(autouse=True)
def _clean_buckets():
    """导出限流桶隔离（跨用例清空进程内滑动窗口，避免顺序敏感）"""
    import app.api.export as export_mod

    export_mod._EXPORT_BUCKETS.clear()
    yield
    export_mod._EXPORT_BUCKETS.clear()


def _new_user(db) -> User:
    user = User(phone=f"exp-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _teardown_user(db, cleanup_user, user) -> None:
    cleanup_user(db, user.id)
    db.delete(user)
    db.commit()


def _seed_full(db, user_id: str) -> None:
    """满库：contents(2) / events(1+成员2) / corrections(2) / profile(+sensitive)"""
    now = datetime.now(timezone.utc)
    c1 = Content(
        id=str(uuid.uuid4()), user_id=user_id, content_type="text", text="备忘标题",
        taken_at=now - timedelta(hours=1), status="done", source="app",
    )
    c2 = Content(
        id=str(uuid.uuid4()), user_id=user_id, content_type="photo",
        taken_at=now - timedelta(days=1), cos_key=f"photos/{user_id}/a.jpg",
        status="done", source="app",
    )
    db.add_all([c1, c2])
    ev = Event(
        id=str(uuid.uuid4()), user_id=user_id, level=1, title="外出",
        start_time=now - timedelta(days=1), status="confirmed", generated_by="cloud",
    )
    db.add(ev)
    db.flush()
    db.add_all(
        [
            EventItem(event_id=ev.id, content_id=c1.id),
            EventItem(event_id=ev.id, content_id=c2.id),
        ]
    )
    db.add_all(
        [
            CorrectionLog(
                user_id=user_id, content_id=c1.id, old_label="todo",
                new_label="idea", source="active",
            ),
            CorrectionLog(
                user_id=user_id, content_id=None, old_label=None,
                new_label="todo", source="active",
            ),
        ]
    )
    db.add(UserProfile(user_id=user_id, version=1, dimensions={"家庭": ["家人"]}))
    db.add(
        ProfileSensitive(
            user_id=user_id, topic="前任", disposition="forbid", evidence=["别跟我提"]
        )
    )
    db.commit()


# ---------------------------------------------------------------------------
# 鉴权 / 参数
# ---------------------------------------------------------------------------


def test_export_requires_auth(export_client):
    """无 token → 401（AUTH-005）"""
    r = export_client.get("/api/v1/export")
    assert r.status_code == 401


def test_export_bad_format(export_client, cleanup_user):
    """不支持的导出格式 → 422 EXPORT_001（参数非法登记码）"""
    db = SessionLocal()
    user = _new_user(db)
    export_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        r = export_client.get("/api/v1/export", params={"format": "csv"})
        assert r.status_code == 422
        assert r.json()["code"] == "EXPORT_001"
    finally:
        export_client.app.dependency_overrides.clear()
        _teardown_user(db, cleanup_user, user)
        db.close()


# ---------------------------------------------------------------------------
# 空库 / 满库
# ---------------------------------------------------------------------------


def test_export_empty_db(export_client, cleanup_user):
    """空库：四类空数组 + truncated=false + 200 + Content-Disposition"""
    db = SessionLocal()
    user = _new_user(db)
    export_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        r = export_client.get("/api/v1/export")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["truncated"] is False
        assert data["contents"] == []
        assert data["events"] == []
        assert data["corrections"] == []
        assert data["profile"]["dimensions"] == {}
        assert data["profile"]["cold_start_done"] is False
        assert data["profile"]["version"] == 0
        # 隐私：user_id_hashed 为 sha256 前缀，不暴露原始 id
        assert data["user_id_hashed"] and data["user_id_hashed"] != str(user.id)
        assert "attachment" in r.headers.get("content-disposition", "")
        assert r.headers["content-type"].startswith("application/json")
    finally:
        export_client.app.dependency_overrides.clear()
        _teardown_user(db, cleanup_user, user)
        db.close()


def test_export_full_db(export_client, cleanup_user):
    """满库：四类字段齐全 + 事件成员 + 图片 url 引用（不含二进制）"""
    db = SessionLocal()
    user = _new_user(db)
    _seed_full(db, str(user.id))
    export_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        r = export_client.get("/api/v1/export")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["truncated"] is False
        assert len(data["contents"]) == 2
        assert len(data["events"]) == 1
        assert len(data["corrections"]) == 2
        assert data["profile"]["dimensions"] == {"家庭": ["家人"]}
        assert data["profile"]["version"] == 1
        assert data["profile"]["cold_start_done"] is True
        assert len(data["profile"]["sensitive"]) == 1
        assert data["profile"]["sensitive"][0]["topic"] == "前任"

        ev = data["events"][0]
        assert ev["event_id"] and ev["title"] == "外出"
        assert ev["level"] == 1 and ev["start_time"] is not None
        assert len(ev["item_content_ids"]) == 2

        photo = next(c for c in data["contents"] if c["content_type"] == "photo")
        assert photo["url"] == f"photos/{user.id}/a.jpg"
        assert photo["content_id"]
        text = next(c for c in data["contents"] if c["content_type"] == "text")
        assert text["caption"] == "备忘标题"
    finally:
        export_client.app.dependency_overrides.clear()
        _teardown_user(db, cleanup_user, user)
        db.close()


# ---------------------------------------------------------------------------
# 越权隔离 / 截断 / 限流
# ---------------------------------------------------------------------------


def test_export_user_isolation(export_client, cleanup_user):
    """跨用户越权：A 的导出绝不包含 B 的任何数据"""
    db = SessionLocal()
    user_a = _new_user(db)
    user_b = _new_user(db)
    _seed_full(db, str(user_a.id))
    c_b = Content(
        id=str(uuid.uuid4()), user_id=user_b.id, content_type="text", text="B的秘密",
        taken_at=datetime.now(timezone.utc), status="done", source="app",
    )
    db.add(c_b)
    db.commit()

    export_client.app.dependency_overrides[get_current_user] = lambda: user_a
    try:
        r = export_client.get("/api/v1/export")
        assert r.status_code == 200
        data = r.json()
        assert all(c["content_id"] != str(c_b.id) for c in data["contents"])
        assert all("B的秘密" not in (c.get("caption") or "") for c in data["contents"])
        assert data["user_id_hashed"] == _hash_user_id(str(user_a.id))
        assert data["user_id_hashed"] != _hash_user_id(str(user_b.id))
    finally:
        export_client.app.dependency_overrides.clear()
        _teardown_user(db, cleanup_user, user_a)
        _teardown_user(db, cleanup_user, user_b)
        db.close()


def test_export_truncated(export_client, cleanup_user, monkeypatch):
    """每类上限截断：超过 EXPORT_MAX_PER_TYPE → truncated=True 且只返回 cap 条"""
    import app.services.export as export_svc

    monkeypatch.setattr(export_svc, "EXPORT_MAX_PER_TYPE", 3)
    db = SessionLocal()
    user = _new_user(db)
    now = datetime.now(timezone.utc)
    db.add_all(
        [
            Content(
                id=str(uuid.uuid4()), user_id=user.id, content_type="text",
                text=f"t{i}", taken_at=now, status="done", source="app",
            )
            for i in range(5)
        ]
    )
    db.commit()
    export_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        r = export_client.get("/api/v1/export")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["truncated"] is True
        assert len(data["contents"]) == 3
    finally:
        export_client.app.dependency_overrides.clear()
        _teardown_user(db, cleanup_user, user)
        db.close()


def test_export_rate_limited(export_client, cleanup_user, monkeypatch):
    """限流防护：超过每窗口上限 → 429 RATE_LIMITED"""
    import app.api.export as export_mod

    monkeypatch.setattr(export_mod, "EXPORT_MAX_PER_WINDOW", 2)
    db = SessionLocal()
    user = _new_user(db)
    export_client.app.dependency_overrides[get_current_user] = lambda: user
    try:
        for _ in range(2):
            r = export_client.get("/api/v1/export")
            assert r.status_code == 200, r.text
        r = export_client.get("/api/v1/export")
        assert r.status_code == 429
        assert r.json()["code"] == "RATE_LIMITED"
    finally:
        export_client.app.dependency_overrides.clear()
        _teardown_user(db, cleanup_user, user)
        db.close()
