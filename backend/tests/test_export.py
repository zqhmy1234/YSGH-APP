"""数据导出测试（卡E03 W3 · 总账 A9 关单：/api/v1/export 恢复）

覆盖（卡面三用例；执行由主控统一跑）：
  - 鉴权 401：无 token 请求
  - 正常导出：满库四类数据（contents/events+成员/corrections/profile）+ attachment 头
  - 空数据：四类空数组 + truncated=False
fixture 风格对齐 conftest.py（client / auth_headers / cleanup_user）+
test_correction.py（pytestmark=integration + SessionLocal 落库）。
注：export router 已在 main.py 注册（卡E03 域内接线），直接用 client 走全链鉴权。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, CorrectionLog, Event, EventItem, UserProfile
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
def _clean_buckets():
    """导出限流桶隔离（进程内滑动窗口跨用例清空，避免顺序敏感）"""
    import app.api.export as export_mod

    export_mod._EXPORT_BUCKETS.clear()
    yield
    export_mod._EXPORT_BUCKETS.clear()


def _seed_full(db, user_id: str) -> None:
    """满库：contents(2) / events(1+成员2) / corrections(2) / profile(1)"""
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
    db.commit()


def test_export_requires_auth(client):
    """鉴权 401：无 token 请求 → AUTH_005"""
    r = client.get("/api/v1/export")
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"


def test_export_empty_db(client, auth_headers, cleanup_user):
    """空数据：四类空数组 + truncated=false + 200 + Content-Disposition attachment"""
    user_id, headers = auth_headers("exp-empty")
    db = SessionLocal()
    try:
        r = client.get("/api/v1/export", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["truncated"] is False
        assert data["contents"] == []
        assert data["events"] == []
        assert data["corrections"] == []
        assert data["profile"]["dimensions"] == {}
        assert data["profile"]["version"] == 0
        # 隐私：user_id_hashed 为 sha256 前缀，不暴露原始 id
        assert data["user_id_hashed"] and data["user_id_hashed"] != user_id
        assert "attachment" in r.headers.get("content-disposition", "")
        assert r.headers["content-type"].startswith("application/json")
    finally:
        cleanup_user(db, user_id)
        db.close()


def test_export_full_db(client, auth_headers, cleanup_user):
    """正常导出：四类字段齐全 + 事件成员 + 图片 url 引用（不含二进制）"""
    user_id, headers = auth_headers("exp-full")
    db = SessionLocal()
    try:
        _seed_full(db, user_id)
        r = client.get("/api/v1/export", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()
        assert data["truncated"] is False
        assert len(data["contents"]) == 2
        assert len(data["events"]) == 1
        assert len(data["corrections"]) == 2
        assert data["profile"]["dimensions"] == {"家庭": ["家人"]}
        assert data["profile"]["version"] == 1

        ev = data["events"][0]
        assert ev["title"] == "外出"
        assert ev["level"] == 1 and ev["start_time"] is not None
        assert len(ev["item_content_ids"]) == 2

        photo = next(c for c in data["contents"] if c["content_type"] == "photo")
        assert photo["url"] == f"photos/{user_id}/a.jpg"
        assert photo["content_id"]
        text = next(c for c in data["contents"] if c["content_type"] == "text")
        assert text["caption"] == "备忘标题"
    finally:
        cleanup_user(db, user_id)
        db.close()
