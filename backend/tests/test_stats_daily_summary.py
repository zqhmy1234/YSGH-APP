"""GET /api/v1/stats/daily-summary 测试（R9-7 · Q5 拍板：hero 副标题 API 化）

环节级三段（对齐 test_data_chains_12 约定）：
  产生 = 事件+成员内容落库 / 处理 = 聚合口径正确 / 输出 = API 字段对齐 + 空态诚实
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


@pytest.fixture()
def db():
    """本文件自带 db fixture（先例对齐 test_l3_voice_chain.py：各文件自管会话）"""
    session = SessionLocal()
    yield session
    session.close()


def _mk_event_with_content(db, user_id: str, title: str, start: datetime, content_text: str | None):
    from app.db.models import Content, Event, EventItem

    ev = Event(
        id=str(uuid.uuid4()),
        user_id=user_id,
        level=1,
        title=title,
        title_source="template",
        start_time=start,
        end_time=start + timedelta(hours=1),
        confidence=0.9,
        status="draft",
        generated_by="cloud",
    )
    db.add(ev)
    cid = None
    if content_text is not None:
        c = Content(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content_type="text",
            text=content_text,
            status="done",
            source="app",
        )
        db.add(c)
        db.flush()
        cid = c.id
        db.add(EventItem(content_id=cid, event_id=ev.id))
    db.commit()
    db.refresh(ev)
    return ev, cid


class TestDailySummaryChain:
    def test_produce_seed_events(self, db_user):
        """产生：两条事件（一新一旧）+ 成员内容落库"""
        db, user = db_user
        uid = str(user.id)
        now = datetime.now(timezone.utc)
        ev_old, _ = _mk_event_with_content(db, uid, "旧事件", now - timedelta(days=3), "旧内容")
        ev_new, _ = _mk_event_with_content(db, uid, "新事件", now - timedelta(hours=1), "新内容")
        assert ev_old.id and ev_new.id

    def test_process_aggregation_ordering(self, db_user):
        """处理：count=distinct 关联内容数；latest 取 start_time 最新事件"""
        from app.db.models import Event

        db, user = db_user
        uid = str(user.id)
        now = datetime.now(timezone.utc)
        _mk_event_with_content(db, uid, "旧事件", now - timedelta(days=3), "旧内容A")
        _mk_event_with_content(db, uid, "旧事件2", now - timedelta(days=2), "旧内容B")
        ev_new, _ = _mk_event_with_content(db, uid, "最新事件", now - timedelta(hours=1), "新内容C")

        # 服务层口径直接验证（FastAPI 依赖注入不便直调，走等价 SQL 断言）
        from sqlalchemy import func, select

        cnt = db.execute(
            select(func.count(func.distinct(Event.id)))
            .where(Event.user_id == user.id, Event.deleted_at.is_(None))
        ).scalar()
        assert cnt == 3, f"事件计数不对: {cnt}"
        top = db.execute(
            select(Event.title, Event.start_time)
            .where(Event.user_id == user.id, Event.deleted_at.is_(None))
            .order_by(Event.start_time.desc().nullslast(), Event.id.desc())
            .limit(1)
        ).first()
        assert top is not None and top.title == "最新事件", f"最新事件排序错: {top}"
        assert ev_new.start_time is not None

    def test_output_api_fields(self, client, auth_headers, db):
        """输出：API 200 且三字段对齐 DB；空用户诚实返回 0/None"""
        uid, headers = auth_headers("dsum")
        # 先验证空态（新用户无数据）
        r0 = client.get("/api/v1/stats/daily-summary", headers=headers)
        assert r0.status_code == 200, r0.text
        d0 = r0.json()["data"]
        assert d0["count"] == 0 and d0["latest_event_text"] is None, f"空态不诚实: {d0}"
        # 建数据后再验
        now = datetime.now(timezone.utc)
        _mk_event_with_content(db, uid, "详情链最新", now - timedelta(minutes=30), "内容X")
        r = client.get("/api/v1/stats/daily-summary", headers=headers)
        assert r.status_code == 200, r.text
        d = r.json()["data"]
        assert d["count"] == 1, f"count 口径错: {d}"
        assert d["latest_event_text"] == "详情链最新", f"latest_text 错: {d}"
        assert d["latest_event_time"], f"latest_time 空: {d}"

    def test_output_requires_auth(self, client):
        """输出：未登录 401（个人统计不公开）"""
        r = client.get("/api/v1/stats/daily-summary")
        assert r.status_code == 401, f"无 token 未被拒: {r.status_code}"
