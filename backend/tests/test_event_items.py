"""照片→事件反向入口测试（B3-4 · 2026-08-26 Wave2 AgentE 新建）

覆盖：
  - 多对多：一张照片同时属 L1 日卡片 + L2 主题 → 全部返回（按 start_time 倒序）
  - photo_count：事件照片计数（文字成员不计）
  - 归属校验：他人内容 / 不存在内容 / 非法 UUID → 404（服务层 ValueError）
  - 软删事件不参与反向查询
  - API 冒烟：GET /api/v1/contents/{id}/events（mini-app 挂 event_items router，
    不依赖 main.py 接线——集成 Agent merge 时注册到主 app）
前置：PG yishu 库
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event, EventEditLog, EventItem, User
from app.db.session import SessionLocal
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"evit-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(EventEditLog).where(EventEditLog.user_id == user.id))
    db.execute(sa_delete(EventItem).where(EventItem.event_id.in_(
        select(Event.id).where(Event.user_id == user.id)
    )))
    db.execute(sa_delete(Event).where(Event.user_id == user.id))
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _content(db, user_id: str, ts=None, content_type: str = "photo") -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=content_type,
        taken_at=ts or datetime.now(timezone.utc) - timedelta(hours=1),
        status="done",
        source="app",
    )
    db.add(c)
    db.commit()
    return c


def _event(db, user_id: str, level: int, start: datetime | None) -> Event:
    ev = Event(
        user_id=user_id,
        level=level,
        title=f"事件-L{level}",
        title_source="template",
        start_time=start,
        end_time=start + timedelta(hours=2) if start else None,
        confidence=0.6 if level >= 2 else 0.9,
        status="draft",
        generated_by="cloud",
    )
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _link(db, content: Content, ev: Event) -> None:
    db.add(EventItem(content_id=content.id, event_id=ev.id))
    db.commit()


class TestContentEventsService:
    def test_multi_level_events_returned_ordered(self, db_user):
        """多对多：同一照片属 L1 + L2 → 全部返回，start_time 倒序"""
        from app.api.event_items import get_content_events

        db, user = db_user
        base = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        c = _content(db, user.id, ts=base)
        ev_l2 = _event(db, user.id, 2, base)          # start = base
        ev_l1 = _event(db, user.id, 1, base + timedelta(hours=3))  # start = base+3h（更新）
        _link(db, c, ev_l2)
        _link(db, c, ev_l1)

        out = get_content_events(db, str(user.id), c.id)
        assert [e["id"] for e in out] == [str(ev_l1.id), str(ev_l2.id)]  # 倒序：L1 在前
        assert {e["level"] for e in out} == {1, 2}
        assert out[0]["title"] == "事件-L1"
        assert out[0]["title_source"] == "template"

    def test_photo_count_counts_photos_only(self, db_user):
        """photo_count：照片成员计数，文字/语音成员不计"""
        from app.api.event_items import get_content_events

        db, user = db_user
        ev = _event(db, user.id, 1, datetime.now(timezone.utc))
        p1 = _content(db, user.id)
        p2 = _content(db, user.id)
        t1 = _content(db, user.id, content_type="text")
        for c in (p1, p2, t1):
            _link(db, c, ev)

        out = get_content_events(db, str(user.id), p1.id)
        assert len(out) == 1
        assert out[0]["photo_count"] == 2

    def test_foreign_content_rejected(self, db_user):
        """归属校验：他人内容 → ValueError（API 层 404）"""
        from app.api.event_items import get_content_events

        db, user = db_user
        other = User(phone=f"evit-o-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        db.refresh(other)
        try:
            c = _content(db, str(other.id))
            with pytest.raises(ValueError):
                get_content_events(db, str(user.id), c.id)
        finally:
            db.execute(sa_delete(Content).where(Content.user_id == other.id))
            db.delete(other)
            db.commit()

    def test_nonexistent_and_invalid_uuid_rejected(self, db_user):
        """不存在内容 / 非法 UUID → ValueError"""
        from app.api.event_items import get_content_events

        db, user = db_user
        with pytest.raises(ValueError):
            get_content_events(db, str(user.id), str(uuid.uuid4()))
        with pytest.raises(ValueError):
            get_content_events(db, str(user.id), "not-a-uuid")

    def test_deleted_event_excluded(self, db_user):
        """软删事件不参与反向查询（软删 30 天规则）"""
        from datetime import datetime, timedelta, timezone

        from app.api.event_items import get_content_events

        db, user = db_user
        c = _content(db, user.id)
        ev = _event(db, user.id, 1, datetime.now(timezone.utc) - timedelta(hours=5))
        _link(db, c, ev)
        ev.deleted_at = datetime.now(timezone.utc)
        db.commit()

        out = get_content_events(db, str(user.id), c.id)
        assert out == []

    def test_content_without_events_returns_empty(self, db_user):
        """未归属任何事件的照片 → 空数组（非 404）"""
        from app.api.event_items import get_content_events

        db, user = db_user
        c = _content(db, user.id)
        assert get_content_events(db, str(user.id), c.id) == []


class TestContentEventsApi:
    def test_api_returns_events_and_404s(self, db_user):
        """API 冒烟：mini-app（event_items router）→ 200 列表；他人内容/非法 ID → 404"""
        from app.api.event_items import router as event_items_router
        from app.core.errors import install_error_handlers
        from app.core.security import create_access_token
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        db, user = db_user
        app = FastAPI()
        install_error_handlers(app)
        app.include_router(event_items_router)
        client = TestClient(app)
        headers = {"Authorization": "Bearer " + create_access_token(str(user.id))}

        base = datetime(2026, 8, 20, 10, 0, tzinfo=timezone.utc)
        c = _content(db, user.id, ts=base)
        ev = _event(db, user.id, 2, base)
        _link(db, c, ev)

        r = client.get(f"/api/v1/contents/{c.id}/events", headers=headers)
        assert r.status_code == 200, r.text
        data = r.json()["data"]
        assert len(data) == 1
        assert data[0]["id"] == str(ev.id)
        assert data[0]["level"] == 2
        assert data[0]["confidence"] == 0.6

        # 他人内容 → 404
        other = User(phone=f"evit-a-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        db.refresh(other)
        try:
            oc = _content(db, str(other.id))
            r2 = client.get(f"/api/v1/contents/{oc.id}/events", headers=headers)
            assert r2.status_code == 404
            assert r2.json()["code"] == "CONTENT_007"
        finally:
            db.execute(sa_delete(Content).where(Content.user_id == other.id))
            db.delete(other)
            db.commit()

        # 非法 UUID → 404（而非 500）
        r3 = client.get("/api/v1/contents/not-a-uuid/events", headers=headers)
        assert r3.status_code == 404

    def test_api_requires_auth(self, db_user):
        """未登录 → 401"""
        from app.api.event_items import router as event_items_router
        from app.core.errors import install_error_handlers
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        install_error_handlers(app)
        app.include_router(event_items_router)
        client = TestClient(app)
        r = client.get(f"/api/v1/contents/{uuid.uuid4()}/events")
        assert r.status_code == 401
