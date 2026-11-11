"""每日复盘重复与并发执行不得为同一用户/自然日创建第二条消息。"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from threading import Event, Lock
from time import sleep

import pytest
from app.db.models import Content, Message
from app.db.session import SessionLocal
from app.services import notify
from sqlalchemy import func, select

pytestmark = pytest.mark.integration
DAY = date(2026, 11, 10)


def _seed(db, user_id, day=DAY):
    db.add(Content(
        user_id=user_id, content_type="text", source="app", text="维护任务测试",
        status="done", taken_at=datetime(day.year, day.month, day.day, 12, tzinfo=notify.REVIEW_TZ),
    ))
    db.commit()


def _count(db, user_id):
    return db.scalar(select(func.count()).select_from(Message).where(
        Message.user_id == user_id, Message.msg_type == "daily_review",
    ))


def test_repeat_review_does_not_create_second_message(db_user):
    db, user = db_user
    _seed(db, user.id)
    first = notify.generate_daily_review(db, user.id, day=DAY)
    assert first is not None
    first.status = "archived"
    db.commit()
    assert notify.generate_daily_review(db, user.id, day=DAY) is None
    assert _count(db, user.id) == 1
    other_day = date(2026, 11, 11)
    _seed(db, user.id, other_day)
    assert notify.generate_daily_review(db, user.id, day=other_day) is not None
    assert _count(db, user.id) == 2


def test_concurrent_reviews_create_one_message(db_user, monkeypatch):
    db, user = db_user
    user_id = str(user.id)
    _seed(db, user_id)
    first_entered, release, second_started = Event(), Event(), Event()
    guard = Lock()
    original = notify._today_stats

    def pause_first(session, uid, day):
        with guard:
            first = not first_entered.is_set()
            first_entered.set()
        if first:
            assert release.wait(5), "first review was not released"
        return original(session, uid, day)

    monkeypatch.setattr(notify, "_today_stats", pause_first)

    def run(second=False):
        with SessionLocal() as session:
            if second:
                second_started.set()
            return notify.generate_daily_review(session, user_id, day=DAY) is not None

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(run)
        try:
            assert first_entered.wait(5)
            second = pool.submit(run, True)
            assert second_started.wait(5)
            sleep(0.2)  # Let a competing transaction reach the deduplication boundary.
        finally:
            release.set()
        assert sum([first.result(timeout=5), second.result(timeout=5)]) == 1
    db.expire_all()
    assert _count(db, user_id) == 1
