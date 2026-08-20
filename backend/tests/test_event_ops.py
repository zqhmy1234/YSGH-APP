"""事件手动操作测试（B3-5 · 2026-08-20 接线）

覆盖：merge（转移成员+软删源+confirmed+edit_log）/ split（拆出建新事件）/
      confirm（转正+改标题）/ 越权拒绝（他人事件 404）/ 幂等合并
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
    user = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(EventEditLog))
    db.execute(sa_delete(EventItem))
    db.execute(sa_delete(Event))
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _event(db, user_id: str, level: int = 1) -> Event:
    ev = Event(user_id=user_id, level=level, title="测试事件", status="draft", generated_by="cloud")
    db.add(ev)
    db.commit()
    db.refresh(ev)
    return ev


def _content(db, user_id: str, ev: Event, ts=None) -> Content:
    c = Content(
        id=str(uuid.uuid4()), user_id=user_id, content_type="photo",
        taken_at=ts or datetime.now(timezone.utc) - timedelta(hours=1),
        status="done", source="app",
    )
    db.add(c)
    db.commit()
    db.add(EventItem(content_id=c.id, event_id=ev.id))
    db.commit()
    return c


class TestMerge:
    def test_merge_moves_items_and_soft_deletes_source(self, db_user):
        from app.services.events import merge_events

        db, user = db_user
        t = _event(db, user.id)
        s1 = _event(db, user.id)
        s2 = _event(db, user.id)
        c1 = _content(db, user.id, s1)
        c2 = _content(db, user.id, s2)

        result = merge_events(db, str(user.id), str(t.id), [str(s1.id), str(s2.id)])
        assert result.status == "confirmed"  # 用户背书
        # 成员已转移
        items = db.execute(select(EventItem).where(EventItem.event_id == t.id)).scalars().all()
        assert {str(i.content_id) for i in items} == {str(c1.id), str(c2.id)}
        # 源事件软删
        db.refresh(s1)
        db.refresh(s2)
        assert s1.deleted_at is not None
        assert s2.deleted_at is not None
        # edit_log 记录
        logs = db.execute(select(EventEditLog).where(EventEditLog.action == "merge")).scalars().all()
        assert len(logs) == 1

    def test_merge_idempotent_duplicate(self, db_user):
        from app.services.events import merge_events

        db, user = db_user
        t = _event(db, user.id)
        s = _event(db, user.id)
        _content(db, user.id, s)
        merge_events(db, str(user.id), str(t.id), [str(s.id)])
        # 重复合并同一源（源已软删）→ 抛错
        with pytest.raises(ValueError):
            merge_events(db, str(user.id), str(t.id), [str(s.id)])

    def test_merge_foreign_event_rejected(self, db_user):
        from app.services.events import merge_events

        db, user = db_user
        other = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        db.refresh(other)
        t = _event(db, user.id)
        foreign = _event(db, str(other.id))
        with pytest.raises(ValueError):
            merge_events(db, str(user.id), str(t.id), [str(foreign.id)])
        db.execute(sa_delete(Event).where(Event.user_id == str(other.id)))
        db.delete(other)
        db.commit()


class TestSplit:
    def test_split_creates_new_event(self, db_user):
        from app.services.events import split_event

        db, user = db_user
        ev = _event(db, user.id)
        c1 = _content(db, user.id, ev)
        c2 = _content(db, user.id, ev)

        new_ev = split_event(db, str(user.id), str(ev.id), [str(c2.id)])
        assert new_ev.id != ev.id
        assert new_ev.status == "confirmed"
        assert new_ev.generated_by == "user"
        # 原事件只剩 c1
        items = db.execute(select(EventItem).where(EventItem.event_id == ev.id)).scalars().all()
        assert [str(i.content_id) for i in items] == [str(c1.id)]
        # 新事件有 c2
        new_items = db.execute(select(EventItem).where(EventItem.event_id == new_ev.id)).scalars().all()
        assert [str(i.content_id) for i in new_items] == [str(c2.id)]

    def test_split_foreign_content_rejected(self, db_user):
        from app.services.events import split_event

        db, user = db_user
        ev = _event(db, user.id)
        other = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        other_ev = _event(db, str(other.id))
        c = _content(db, str(other.id), other_ev)
        with pytest.raises(ValueError):
            split_event(db, str(user.id), str(ev.id), [str(c.id)])
        db.execute(sa_delete(EventItem).where(EventItem.event_id == str(other_ev.id)))
        db.execute(sa_delete(Content).where(Content.user_id == str(other.id)))
        db.execute(sa_delete(Event).where(Event.user_id == str(other.id)))
        db.delete(other)
        db.commit()


class TestConfirm:
    def test_confirm_sets_status_and_title(self, db_user):
        from app.services.events import confirm_event

        db, user = db_user
        ev = _event(db, user.id)
        result = confirm_event(db, str(user.id), str(ev.id), title="家庭聚会")
        assert result.status == "confirmed"
        assert result.title == "家庭聚会"
        assert result.title_source == "user"
        assert result.confidence == 1.0

    def test_confirm_foreign_rejected(self, db_user):
        from app.services.events import confirm_event

        db, user = db_user
        other = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        foreign = _event(db, str(other.id))
        with pytest.raises(ValueError):
            confirm_event(db, str(user.id), str(foreign.id))
        db.execute(sa_delete(Event).where(Event.user_id == str(other.id)))
        db.delete(other)
        db.commit()
