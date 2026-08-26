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
from sqlalchemy import or_, select

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    # TD-P1C（2026-08-26）：全表删除 → 按测试用户过滤——防误删他人/存量数据
    # EventItem 无 user_id，按 event_id∈本人事件 OR content_id∈本人内容 清理
    db.execute(sa_delete(EventEditLog).where(EventEditLog.user_id == user.id))
    db.execute(sa_delete(EventItem).where(or_(
        EventItem.event_id.in_(select(Event.id).where(Event.user_id == user.id)),
        EventItem.content_id.in_(select(Content.id).where(Content.user_id == user.id)),
    )))
    db.execute(sa_delete(Event).where(Event.user_id == user.id))
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

    def test_merge_window_covers_merged_content(self, db_user):
        """2026-08-25 修复回归：合并后 target 时间窗必须覆盖并入内容

        此前 autoflush=False 导致 _refresh_event_window 查不到新转移的成员 →
        窗口漏算并入内容（标题条数也不更新）。
        """
        from app.services.events import merge_events

        db, user = db_user
        t = _event(db, user.id)
        s = _event(db, user.id)
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        tc = _content(db, user.id, t, ts=base)
        sc = _content(db, user.id, s, ts=base + timedelta(hours=2))
        t.title_source = "template"  # 触发 _refresh_event_window 重算标题
        db.commit()

        result = merge_events(db, str(user.id), str(t.id), [str(s.id)])
        db.refresh(result)
        assert result.start_time == tc.taken_at
        assert result.end_time == sc.taken_at
        assert result.title.endswith("2条")

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

    def test_split_new_event_window_from_moved_content(self, db_user):
        """2026-08-25 修复回归：拆出新事件的时间窗必须来自拆出内容

        此前 autoflush=False 导致 _refresh_event_window 查不到新成员 →
        start_time=None → 时间轴分组到 1970/1月1日（真机拆分子验证暴露）。
        """
        from app.services.events import split_event

        db, user = db_user
        ev = _event(db, user.id)
        base = datetime.now(timezone.utc) - timedelta(hours=3)
        c1 = _content(db, user.id, ev, ts=base)
        c2 = _content(db, user.id, ev, ts=base + timedelta(hours=1))
        c3 = _content(db, user.id, ev, ts=base + timedelta(hours=2))
        ev.title_source = "template"  # 触发 _refresh_event_window 重算标题
        db.commit()

        new_ev = split_event(db, str(user.id), str(ev.id), [str(c2.id), str(c3.id)])
        db.refresh(new_ev)
        # 修复回归：新事件时间窗来自拆出内容（此前 autoflush=False → start_time=None）
        assert new_ev.start_time == c2.taken_at
        assert new_ev.end_time == c3.taken_at
        # 原事件窗口收窄为剩余成员 + 模板标题条数更新
        db.refresh(ev)
        assert ev.start_time == c1.taken_at
        assert ev.end_time == c1.taken_at
        assert ev.title.endswith("1条")
        # 新事件 title_source=user（用户拆分保留标题），不参与模板重算

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

class TestEventItems:
    def test_items_ordered_by_taken_at(self, db_user):
        from app.services.events import get_event_items

        db, user = db_user
        ev = _event(db, user.id)
        c1 = _content(db, user.id, ev, ts=datetime.now(timezone.utc) - timedelta(hours=2))
        c2 = _content(db, user.id, ev, ts=datetime.now(timezone.utc) - timedelta(hours=1))
        c1.text = "第一张照片的caption"
        c2.text = "第二张照片的caption"
        db.commit()
        items = get_event_items(db, str(user.id), str(ev.id))
        assert [i["content_id"] for i in items] == [str(c1.id), str(c2.id)]
        assert items[0]["title"] == "第一张照片的caption"
        assert items[0]["content_type"] == "photo"

    def test_items_foreign_event_rejected(self, db_user):
        from app.services.events import get_event_items

        db, user = db_user
        other = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        foreign = _event(db, str(other.id))
        with pytest.raises(ValueError):
            get_event_items(db, str(user.id), str(foreign.id))
        db.execute(sa_delete(Event).where(Event.user_id == str(other.id)))
        db.delete(other)
        db.commit()


class TestSetCover:
    def test_set_cover_to_member_and_clear(self, db_user):
        """B3-4 用户换封面：cover 必须是事件成员；可清空"""
        from app.services.events import set_event_cover

        db, user = db_user
        ev = _event(db, user.id)
        c2 = _content(db, user.id, ev)
        r = set_event_cover(db, str(user.id), str(ev.id), str(c2.id))
        assert str(r.cover_content_id) == str(c2.id)
        r2 = set_event_cover(db, str(user.id), str(ev.id), None)
        assert r2.cover_content_id is None
        # 编辑痕迹
        logs = db.execute(select(EventEditLog).where(EventEditLog.action == "set_cover")).scalars().all()
        assert len(logs) == 2

    def test_set_cover_foreign_member_rejected(self, db_user):
        from app.services.events import set_event_cover

        db, user = db_user
        ev = _event(db, user.id)
        other = User(phone=f"evt-{uuid.uuid4().hex[:8]}", status=1)
        db.add(other)
        db.commit()
        other_ev = _event(db, str(other.id))
        c = _content(db, str(other.id), other_ev)
        with pytest.raises(ValueError):
            set_event_cover(db, str(user.id), str(ev.id), str(c.id))
        db.execute(sa_delete(EventItem).where(EventItem.event_id == str(other_ev.id)))
        db.execute(sa_delete(Content).where(Content.user_id == str(other.id)))
        db.execute(sa_delete(Event).where(Event.user_id == str(other.id)))
        db.delete(other)
        db.commit()


class TestConfirmedProtection:
    def test_l3_skip_when_confirmed_user_event_has_tag(self, db_user):
        """B3-5 confirmed 保护：用户已确认（confirmed + user 标题含标签）→ L3 同名不重建"""
        from app.services.events import _write_upper_candidates

        db, user = db_user
        # 用户已确认一个标题含"家庭"的 L2 事件（改名后标题变化场景）
        db.add(Event(
            user_id=user.id, level=2, title="2026 家庭大聚会", title_source="user",
            status="confirmed", confidence=1.0, generated_by="user",
        ))
        db.commit()
        cand = {
            "tag": "家庭", "count": 5, "total_photos": 5,
            "time_range": ["2026-08-01T10:00:00+00:00", "2026-08-03T10:00:00+00:00"],
            "cluster": [], "cover_content_id": None,
        }
        added = _write_upper_candidates(db, str(user.id), [], [cand])
        assert added == 0, "用户已确认同标签事件 → L3 不重建"
        l3 = db.execute(
            select(Event).where(Event.user_id == user.id, Event.level == 3)
        ).scalars().all()
        assert l3 == []

    def test_l3_skip_when_members_linked_to_confirmed_event(self, db_user):
        """B3-5 confirmed 保护：候选成员已挂用户确认事件 → L3 不重建"""
        from app.services.events import _write_upper_candidates

        db, user = db_user
        confirmed = Event(
            user_id=user.id, level=2, title="考研备考", title_source="user",
            status="confirmed", confidence=1.0, generated_by="user",
        )
        db.add(confirmed)
        db.commit()
        c = Content(id=str(uuid.uuid4()), user_id=user.id, content_type="photo",
                    taken_at=datetime.now(timezone.utc), status="done", source="app")
        db.add(c)
        db.add(EventItem(content_id=c.id, event_id=confirmed.id))
        db.commit()
        cand = {
            "tag": "备考", "count": 3, "total_photos": 3,
            "time_range": ["2026-08-01T10:00:00+00:00", "2026-08-02T10:00:00+00:00"],
            "cluster": [str(c.id)], "cover_content_id": None,
        }
        added = _write_upper_candidates(db, str(user.id), [], [cand])
        assert added == 0, "成员已挂用户背书事件 → L3 不重建"

    def test_l1_no_append_to_confirmed_user_event(self, db_user):
        """B3-5 confirmed 保护：用户背书 L1 不再被算法追加成员/更新时间窗"""
        from app.services.events import _write_l1_days

        db, user = db_user
        base = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
        ev = Event(
            user_id=user.id, level=1, title="自定义标题", title_source="user",
            status="confirmed", confidence=1.0, generated_by="user",
            start_time=base, end_time=base,
        )
        db.add(ev)
        db.commit()
        c = Content(id=str(uuid.uuid4()), user_id=user.id, content_type="photo",
                    taken_at=base + timedelta(hours=2), status="done", source="app")
        db.add(c)
        db.commit()
        day = {
            "date": "2026-08-01",
            "photos": [type("P", (), {"id": str(c.id), "ts": c.taken_at})()],
            "is_sparse": False,
        }
        items = _write_l1_days(db, str(user.id), [day])
        assert items == 0, "用户背书 L1 不应追加成员"
        db.refresh(ev)
        assert ev.start_time == base, "用户背书 L1 时间窗不应被算法改动"
