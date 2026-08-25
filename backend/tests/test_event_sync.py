"""端侧事件同步测试（S-SY-1 · B3-6 端侧 L0/L1 真值）

覆盖：
  - client_event_id 幂等：同一事件重发只落一次（网络重试）
  - 照片归属校验：他人照片 / 不存在照片 → 整条 rejected
  - 空 photo_ids → rejected
  - 落库：L1 事件 generated_by=device + event_items 关联 + 变更日志（offline_queue）
  - 云侧只跑 L2/L3：跨天同标签照片提交后产生 level=2 候选
  - 时间轴可见：sync 后 GET /timeline 返回该事件
  - 并发同 client_event_id：唯一索引兜底（IntegrityError → 幂等重试）
前置：PG yishu 库
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event, EventItem, OfflineQueue, User
from app.db.session import SessionLocal
from sqlalchemy import delete as sa_delete
from sqlalchemy import select

pytestmark = pytest.mark.integration

DEVICE = "test-device-eventsync"


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"evsync-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(EventItem).where(EventItem.event_id.in_(
        select(Event.id).where(Event.user_id == user.id)
    )))
    db.execute(sa_delete(Event).where(Event.user_id == user.id))
    db.execute(sa_delete(OfflineQueue).where(OfflineQueue.user_id == user.id))
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _photo(db, user_id: str, taken_at: datetime, tags: list[str] | None = None) -> str:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo",
        status="done",
        source="app",
        taken_at=taken_at,
        extra={"ci_tags": tags} if tags else None,
    )
    db.add(c)
    db.commit()
    return str(c.id)


def _ts(days: int = 0, hours: int = 0) -> str:
    return (datetime.now(timezone.utc) + timedelta(days=days, hours=hours)).isoformat()


def _client_event(cid: str, photo_ids: list[str], start: str | None = None, **kw) -> dict:
    ev = {
        "client_event_id": cid,
        "title": kw.get("title"),
        "start_time": start or _ts(),
        "end_time": kw.get("end_time"),
        "place": kw.get("place"),
        "photo_ids": photo_ids,
    }
    return {k: v for k, v in ev.items() if v is not None}


def test_sync_idempotent_by_client_event_id(db_user):
    """幂等：同一 client_event_id 重发 → duplicates，不重复落库"""
    from app.services.events import sync_client_events

    db, user = db_user
    pid = _photo(db, user.id, datetime.now(timezone.utc))
    ev = _client_event(f"ev-{uuid.uuid4().hex[:8]}", [pid], _ts())
    r1 = sync_client_events(db, user.id, DEVICE, [ev])
    r2 = sync_client_events(db, user.id, DEVICE, [ev])  # 重发
    assert len(r1["accepted"]) == 1
    assert r2["duplicates"] == [ev["client_event_id"]]
    assert len(r2["accepted"]) == 0
    count = db.execute(
        select(Event).where(Event.user_id == user.id, Event.level == 1)
    ).scalars().all()
    assert len(count) == 1


def test_sync_rejects_foreign_photo(db_user):
    """归属校验：他人照片 → 整条 rejected"""
    from app.services.events import sync_client_events

    db, user = db_user
    other = User(phone=f"evsync-other-{uuid.uuid4().hex[:8]}", status=1)
    db.add(other)
    db.commit()
    db.refresh(other)
    try:
        pid = _photo(db, other.id, datetime.now(timezone.utc))
        ev = _client_event(f"ev-{uuid.uuid4().hex[:8]}", [pid], _ts())
        r = sync_client_events(db, user.id, DEVICE, [ev])
        assert len(r["rejected"]) == 1
        assert "不属于" in r["rejected"][0]["reason"]
        assert db.execute(
            select(Event).where(Event.user_id == user.id, Event.level == 1)
        ).scalars().all() == []
    finally:
        db.execute(sa_delete(Content).where(Content.user_id == other.id))
        db.delete(other)
        db.commit()


def test_sync_rejects_nonexistent_and_empty(db_user):
    """不存在照片 / 空 photo_ids → rejected"""
    from app.services.events import sync_client_events

    db, user = db_user
    ghost = str(uuid.uuid4())
    r1 = sync_client_events(db, user.id, DEVICE, [_client_event("ev-ghost", [ghost], _ts())])
    assert len(r1["rejected"]) == 1 and "不属于" in r1["rejected"][0]["reason"]
    r2 = sync_client_events(db, user.id, DEVICE, [{
        "client_event_id": "ev-empty", "start_time": _ts(), "photo_ids": [],
    }])
    assert len(r2["rejected"]) == 1 and "为空" in r2["rejected"][0]["reason"]


def test_sync_creates_l1_device_event_with_changelog(db_user):
    """落库：L1 事件 generated_by=device + event_items + offline_queue 变更日志"""
    from app.services.events import sync_client_events

    db, user = db_user
    now = datetime.now(timezone.utc)
    pids = [_photo(db, user.id, now + timedelta(minutes=i)) for i in range(3)]
    ev = _client_event("ev-full", pids, now.isoformat(), title="2026-08-24 · 3条", place="测试地")
    r = sync_client_events(db, user.id, DEVICE, [ev])
    assert len(r["accepted"]) == 1

    row = db.execute(
        select(Event).where(Event.user_id == user.id, Event.level == 1)
    ).scalar_one()
    assert row.generated_by == "device"
    assert row.client_event_id == "ev-full"
    assert row.place == "测试地"
    items = db.execute(
        select(EventItem.content_id).where(EventItem.event_id == row.id)
    ).scalars().all()
    assert sorted(str(i) for i in items) == sorted(pids)
    # 变更日志（其他端增量拉取源 → M4 端间一致）
    logs = db.execute(
        select(OfflineQueue).where(OfflineQueue.user_id == user.id)
    ).scalars().all()
    assert len(logs) == 1
    assert logs[0].op_type == "upsert_event"
    assert logs[0].payload["entity_id"] == str(row.id)
    assert set(logs[0].payload["value"]["photo_ids"]) == set(pids)


def test_sync_triggers_l2_candidates(db_user):
    """云侧只跑 L2/L3：跨天 ≥2 天 ≥10 张同标签照片 → level=2 候选落库"""
    from app.services.events import sync_client_events

    db, user = db_user
    base = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    pids = []
    for day in range(2):
        for i in range(6):  # 12 张跨 2 天，同标签
            pids.append(_photo(db, user.id, base + timedelta(days=day, minutes=i * 5), tags=["美食"]))
    ev = _client_event("ev-l2", pids, base.isoformat(),
                       end_time=(base + timedelta(days=1, minutes=30)).isoformat())
    r = sync_client_events(db, user.id, DEVICE, [ev])
    assert r["upper_items"] >= 10, f"L2 候选应新增成员，实际 {r['upper_items']}"
    l2 = db.execute(
        select(Event).where(Event.user_id == user.id, Event.level == 2)
    ).scalars().all()
    assert len(l2) >= 1
    # L1 关联不拦截：照片同时挂 L1 与 L2（B3-6 层级独立）
    l2_items = db.execute(
        select(EventItem.content_id).where(EventItem.event_id == l2[0].id)
    ).scalars().all()
    assert len(l2_items) >= 10


def test_sync_idempotent_does_not_resync_l2(db_user):
    """幂等重发：不重复触发 L2/L3（候选生成幂等）"""
    from app.services.events import sync_client_events

    db, user = db_user
    base = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    pids = [_photo(db, user.id, base + timedelta(days=d, minutes=i * 5), tags=["旅行"])
            for d in range(2) for i in range(6)]
    ev = _client_event("ev-l2b", pids, base.isoformat())
    r1 = sync_client_events(db, user.id, DEVICE, [ev])
    r2 = sync_client_events(db, user.id, DEVICE, [ev])
    assert r1["upper_items"] >= 10
    assert r2["duplicates"] == ["ev-l2b"]
    assert r2["upper_items"] == 0, "重发不应重复生成候选"
    l2 = db.execute(
        select(Event).where(Event.user_id == user.id, Event.level == 2)
    ).scalars().all()
    assert len(l2) == 1


def test_sync_via_api_and_timeline(db_user):
    """API 冒烟：POST /events/sync → 时间轴可见"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    client = TestClient(app)
    # dev mock 登录（code 任意，mock-unionid-{code} 派生；登录用户 ≠ fixture 用户）
    code = f"api-{uuid.uuid4().hex[:8]}"
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": code, "device_id": DEVICE},
    )
    assert r.status_code == 200, r.text
    token = r.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    api_user = db.execute(
        select(User).where(User.unionid == f"mock-unionid-{code}")
    ).scalar_one()
    pid = _photo(db, str(api_user.id), datetime.now(timezone.utc))
    ev = _client_event("ev-api", [pid], _ts())
    r = client.post("/api/v1/events/sync", json={"device_id": DEVICE, "events": [ev]}, headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert len(data["accepted"]) == 1
    # 时间轴可见（L1）
    r = client.get("/api/v1/events/timeline?level=1", headers=headers)
    assert r.status_code == 200
    events = r.json()["data"]
    assert any(e["id"] == data["accepted"][0]["event_id"] for e in events)


def test_sync_concurrent_same_client_event_id(db_user):
    """并发同 client_event_id：唯一索引兜底（幂等检查之外的竞态防线）"""
    from app.services.events import sync_client_events
    from sqlalchemy.exc import IntegrityError

    db, user = db_user
    pid = _photo(db, user.id, datetime.now(timezone.utc))
    ev = _client_event("ev-race", [pid], _ts())
    r1 = sync_client_events(db, user.id, DEVICE, [ev])
    assert len(r1["accepted"]) == 1
    # 模拟并发竞态：第二条请求绕过幂等检查直接插入 → 唯一索引必须拦截
    db.add(Event(
        user_id=user.id, level=1, title="并发副本", generated_by="device",
        client_event_id="ev-race", start_time=datetime.now(timezone.utc),
    ))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    # 兜底后数据仍只有一条
    rows = db.execute(
        select(Event).where(Event.user_id == user.id, Event.client_event_id == "ev-race")
    ).scalars().all()
    assert len(rows) == 1


# --- Wave2-AgentD：L2 LLM 归并裁决（mock 通道）/ 待确认区 / L3 主题流成员 / 生命周期 ---

def test_sync_l2_mock_verdict_promotes_tag_consistent(db_user):
    """L2 mock 裁决：标签一致候选 → confidence 0.8 → 转正（confirmed）+ 封面赋值（B3-4）"""
    from app.services.events import sync_client_events

    db, user = db_user
    base = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    pids = [_photo(db, user.id, base + timedelta(days=d, minutes=i * 5), tags=["美食"])
            for d in range(2) for i in range(6)]
    r = sync_client_events(db, user.id, DEVICE, [_client_event("ev-promote", pids, base.isoformat())])
    assert r["upper_items"] >= 10
    l2 = db.execute(select(Event).where(Event.user_id == user.id, Event.level == 2)).scalars().all()
    assert len(l2) == 1
    assert l2[0].status == "confirmed", "≥0.7 转正"
    assert l2[0].confidence == 0.8
    assert l2[0].title_source == "template"   # mock → 模板标题（真实 LLM 为 llm）
    assert l2[0].cover_content_id is not None, "B3-4 封面应赋值"


def test_sync_l2_nontag_stays_pending(db_user):
    """L2 待确认区：无标签候选 → confidence 0.6 → draft；timeline pending=true 可见"""
    from app.services.events import get_timeline, sync_client_events

    db, user = db_user
    base = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    pids = [_photo(db, user.id, base + timedelta(days=d, minutes=i * 5))
            for d in range(2) for i in range(6)]   # 无标签无 GPS → 内容维/时间维候选
    r = sync_client_events(db, user.id, DEVICE, [_client_event("ev-pending", pids, base.isoformat())])
    assert r["upper_items"] >= 10
    l2 = db.execute(select(Event).where(Event.user_id == user.id, Event.level == 2)).scalars().all()
    assert len(l2) == 1
    assert l2[0].status == "draft" and l2[0].confidence == 0.6, "<0.7 保持 draft 进待确认"
    pending = get_timeline(db, str(user.id), pending=True)
    assert any(e.id == l2[0].id for e in pending)
    assert not any(e.id == l2[0].id for e in get_timeline(db, str(user.id), pending=True, status="confirmed"))


def test_sync_creates_l3_stream_with_members(db_user):
    """L3 7 天窗：同标签 7 天内 ≥3 次（跨天）→ 主题流落库并真实挂成员"""
    from app.services.events import get_event_last_activity, sync_client_events

    db, user = db_user
    base = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    pids = [_photo(db, user.id, base + timedelta(days=d), tags=["备考"]) for d in range(3)]
    r = sync_client_events(db, user.id, DEVICE, [_client_event("ev-l3", pids, base.isoformat())])
    assert r["upper_items"] >= 3
    l3 = db.execute(select(Event).where(Event.user_id == user.id, Event.level == 3)).scalars().all()
    assert len(l3) == 1
    assert l3[0].title == "标签 · 备考"
    assert l3[0].start_time is not None
    assert l3[0].cover_content_id is not None, "L3 独立封面"
    items = db.execute(select(EventItem.content_id).where(EventItem.event_id == l3[0].id)).scalars().all()
    assert sorted(str(i) for i in items) == sorted(pids), "L3 主题流真实挂成员（生命周期可派生）"
    last = get_event_last_activity(db, str(user.id), [str(l3[0].id)])
    assert str(l3[0].id) in last and last[str(l3[0].id)] is not None


def test_l3_lifecycle_via_timeline_api(db_user):
    """L3 生命周期：归档流（成员 200 天前）经 timeline API 派生输出 archived"""
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    client = TestClient(app)
    code = f"api-life-{uuid.uuid4().hex[:8]}"
    r = client.post("/api/v1/auth/wechat", json={"code": code, "device_id": DEVICE})
    assert r.status_code == 200, r.text
    token = r.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    api_user = db.execute(select(User).where(User.unionid == f"mock-unionid-{code}")).scalar_one()
    old = datetime(2024, 1, 1, 9, 0, tzinfo=timezone.utc)
    pids = [_photo(db, str(api_user.id), old + timedelta(days=d), tags=["旧流"]) for d in range(3)]
    ev = Event(
        user_id=api_user.id, level=3, title="标签 · 旧流", title_source="template",
        start_time=old, end_time=old + timedelta(days=2), status="draft", generated_by="cloud-proto",
    )
    db.add(ev)
    db.flush()
    for pid in pids:
        db.add(EventItem(content_id=pid, event_id=ev.id))
    db.commit()
    r = client.get("/api/v1/events/timeline?level=3", headers=headers)
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    ev_out = next(e for e in data if e["id"] == str(ev.id))
    assert ev_out["lifecycle"]["state"] == "archived", f"归档流应 archived，实际 {ev_out['lifecycle']}"
