"""性能优化端点测试（性能卡 PG）：GET /messages/unread-count + GET /events/stats

覆盖：
  - 鉴权 401：无 token 请求
  - 空数据：新用户 count=0 / stats 全 0
  - 有数据：未读口径 status=unread / 软删事件排除 / distinct 照片 / 用户隔离
fixture 风格对齐 conftest.py（client / auth_headers / cleanup_user）+
test_export.py（pytestmark=integration + SessionLocal 落库）。
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event, EventItem, Message
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration


def _seed(db, user_id: str, other_id: str) -> None:
    """落库：3 个有效事件（2 天）+ 1 个软删事件 + 4 照片/1 文字 + 未读/已读消息"""
    day = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)
    ev_same_day_1 = Event(id=str(uuid.uuid4()), user_id=user_id, start_time=day, status="confirmed")
    ev_same_day_2 = Event(  # 同天 → day_count 不重复计
        id=str(uuid.uuid4()), user_id=user_id, start_time=day + timedelta(hours=2), status="confirmed"
    )
    ev_next_day = Event(
        id=str(uuid.uuid4()), user_id=user_id, start_time=day + timedelta(days=1), status="confirmed"
    )
    ev_deleted = Event(
        id=str(uuid.uuid4()), user_id=user_id, start_time=day, deleted_at=datetime.now(timezone.utc)
    )
    photo_a = Content(id=str(uuid.uuid4()), user_id=user_id, content_type="photo", status="done")
    photo_b = Content(id=str(uuid.uuid4()), user_id=user_id, content_type="photo", status="done")
    photo_on_deleted = Content(id=str(uuid.uuid4()), user_id=user_id, content_type="photo", status="done")
    text_item = Content(id=str(uuid.uuid4()), user_id=user_id, content_type="text", status="done")
    db.add_all([ev_same_day_1, ev_same_day_2, ev_next_day, ev_deleted])
    db.add_all([photo_a, photo_b, photo_on_deleted, text_item])
    db.flush()
    db.add_all([
        EventItem(content_id=photo_a.id, event_id=ev_same_day_1.id),
        EventItem(content_id=photo_b.id, event_id=ev_same_day_2.id),
        EventItem(content_id=text_item.id, event_id=ev_same_day_1.id),  # 文字不计 photo_count
        EventItem(content_id=photo_on_deleted.id, event_id=ev_deleted.id),  # 软删事件不计
    ])
    db.add_all([
        Message(user_id=user_id, msg_type="care_followup", title="t", body="b", status="unread"),
        Message(user_id=user_id, msg_type="care_followup", title="t", body="b", status="unread"),
        Message(user_id=user_id, msg_type="echo", title="t", body="b", status="read"),
        Message(user_id=other_id, msg_type="echo", title="t", body="b", status="unread"),  # 他人不计入
    ])
    db.commit()


def test_perf_endpoints_requires_auth(client):
    """鉴权 401：无 token 请求 → AUTH_005（两端点）"""
    for path in ("/api/v1/messages/unread-count", "/api/v1/events/stats"):
        r = client.get(path)
        assert r.status_code == 401, r.text
        assert r.json()["code"] == "AUTH_005"


def test_perf_endpoints_empty_user(client, auth_headers, cleanup_user):
    """空数据：unread-count=0；stats 四字段全 0"""
    user_id, headers = auth_headers("perf-empty")
    db = SessionLocal()
    try:
        r = client.get("/api/v1/messages/unread-count", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["count"] == 0

        r = client.get("/api/v1/events/stats", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {
            "event_count": 0,
            "day_count": 0,
            "photo_count": 0,
            "unread_count": 0,
        }
    finally:
        cleanup_user(db, user_id)
        db.close()


def test_unread_count_with_data(client, auth_headers, cleanup_user):
    """有数据：只计 status=unread 且本人消息（他人未读/本人已读不计）"""
    user_id, headers = auth_headers("perf-unread")
    other_id, _ = auth_headers("perf-unread-other")
    db = SessionLocal()
    try:
        _seed(db, user_id, other_id)
        r = client.get("/api/v1/messages/unread-count", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["count"] == 2
    finally:
        cleanup_user(db, user_id)
        cleanup_user(db, other_id)
        db.close()


def test_stats_with_data(client, auth_headers, cleanup_user):
    """有数据：软删事件排除 / 按天去重 / distinct 照片 / 用户隔离"""
    user_id, headers = auth_headers("perf-stats")
    other_id, other_headers = auth_headers("perf-stats-other")
    db = SessionLocal()
    try:
        _seed(db, user_id, other_id)
        r = client.get("/api/v1/events/stats", headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {
            "event_count": 3,  # 4 个事件 - 1 软删
            "day_count": 2,  # 09-01（两个同天事件）+ 09-02
            "photo_count": 2,  # 2 张有效照片；文字不计、软删事件照片不计
            "unread_count": 2,  # 本人的 2 条 unread；他人消息不计
        }
        # 用户隔离：另一用户只见自己的 1 条 unread，事件全 0
        r = client.get("/api/v1/events/stats", headers=other_headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"] == {
            "event_count": 0,
            "day_count": 0,
            "photo_count": 0,
            "unread_count": 1,
        }
    finally:
        cleanup_user(db, user_id)
        cleanup_user(db, other_id)
        db.close()
