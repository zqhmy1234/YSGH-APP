"""F-Events（F5 events.py 拆包 + F3 聚合入队契约）测试

锚定：
  - process_content 契约：主提交后按 user 级 key 入队聚合（不再同步跑聚合；
    入队失败不否定主转写）
  - RQ 任务可解析：run_user_aggregation 模块路径 + events 包重导出 + worker 登记

F3 聚合专属测试（任务单测 / per-user 去重并发 / _write_upper_candidates 幂等）
见 tests/test_aggregation.py。
前置：PG yishu 库（db_user 公共 fixture）
"""
import uuid

import pytest
from app.db.models import Content

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# F3/R5-3：process_content 契约（主提交后按 user 级 key 入队）+ worker 登记
# ---------------------------------------------------------------------------


def test_process_content_queues_per_user_aggregation(db_user, monkeypatch):
    """F3：process_content 主提交后按 user 级 key 入队聚合（不再同步跑聚合）"""
    db, user = db_user
    c = Content(
        id=str(uuid.uuid4()), user_id=user.id, content_type="text",
        text="今天想吃火锅", status="processing", source="app",
    )
    db.add(c)
    db.commit()
    monkeypatch.setattr(
        "app.services.pipeline._get_classifier",
        lambda: lambda t: {"label": "todo", "label_cn": "待办", "confidence": 0.95},
    )
    calls: list[tuple] = []

    def fake_enqueue(func, key, *a, **kw):
        calls.append((func, key, a, kw))
        return {"job_id": "x"}

    monkeypatch.setattr("app.core.queue.enqueue_unique", fake_enqueue)
    from app.services.pipeline import process_content

    r = process_content(str(c.id))
    assert r["status"] == "done"
    assert r["agg_job"] == "queued"
    assert "events_queued" in r["processed"]
    # BA3（A1 打标）后 text 内容也投 tag_content（key=content id）——恰 2 次入队
    assert len(calls) == 2, f"text 应恰有打标+聚合两次入队，实际 {calls}"
    tag_call = next(c for c in calls if c[0].__name__ == "tag_content")
    assert tag_call[1] == str(c.id), f"打标 key 应为 content id，实际 {tag_call[1]}"
    assert tag_call[2] == (str(c.id),), f"打标应透传 content_id 参数，实际 {tag_call[2]}"
    func, key, a, kw = next(c for c in calls if c[0].__name__ == "run_user_aggregation")
    assert func.__name__ == "run_user_aggregation"
    assert key == f"user:{user.id}"
    assert a == (str(user.id),)
    assert kw.get("mode") == "l2l3"
    assert kw.get("queue_name") == "low"
    assert kw.get("job_timeout") == 300


def test_process_content_agg_enqueue_failure_keeps_done(db_user, monkeypatch):
    """F3：聚合入队失败不否定主转写（聚合失败静默语义）"""
    db, user = db_user
    c = Content(
        id=str(uuid.uuid4()), user_id=user.id, content_type="text",
        text="测试", status="processing", source="app",
    )
    db.add(c)
    db.commit()
    monkeypatch.setattr(
        "app.services.pipeline._get_classifier",
        lambda: lambda t: {"label": "todo", "label_cn": "待办", "confidence": 0.95},
    )

    def fail_enqueue(*args, **kwargs):
        raise RuntimeError("redis unavailable")

    monkeypatch.setattr("app.core.queue.enqueue_unique", fail_enqueue)
    from app.services.pipeline import process_content

    r = process_content(str(c.id))
    assert r["status"] == "done"
    assert r["agg_job"] == "enqueue_failed"
    db.refresh(c)
    assert c.status == "done"


def test_aggregation_task_importable_for_rq():
    """F3：聚合任务按 RQ 模块路径可解析 + events 包重导出 + worker 登记"""
    from app.services.events import run_user_aggregation
    from app.services.events.aggregate import run_user_aggregation as _impl

    assert _impl.__module__ == "app.services.events.aggregate"
    assert run_user_aggregation is _impl
    # worker 进程入口已登记（RQ 反序列化依赖模块路径可导入）
    import app.workers.worker

    assert hasattr(app.workers.worker, "main")


# ---------------------------------------------------------------------------
# A6（2026-09-09 缺口收口）：timeline voice 出参带 duration（契约 "m:ss"）
# ---------------------------------------------------------------------------


def test_timeline_voice_duration_formatted(db_user):
    """timeline 出参 voice.duration == "0:05"（构造 duration=5s 语音挂进事件）"""
    from datetime import datetime, timezone

    from app.core.security import create_access_token
    from app.db.models import Event, EventItem
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    now = datetime.now(timezone.utc)
    event = Event(user_id=user.id, level=1, title="A6", start_time=now, status="confirmed")
    db.add(event)
    voice = Content(
        id=str(uuid.uuid4()), user_id=user.id, content_type="voice",
        text="语音记忆正文", duration=5, status="done", source="app",
        taken_at=now,
    )
    db.add(voice)
    db.commit()
    db.add(EventItem(event_id=event.id, content_id=voice.id))
    db.commit()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    r = client.get("/api/v1/events/timeline", headers=headers)
    assert r.status_code == 200, r.text
    target = next(
        (e for e in r.json()["data"] if e["id"] == str(event.id)), None
    )
    assert target is not None, f"timeline 应含事件: {[e['id'] for e in r.json()['data']]}"
    voice_out = target["voice"]
    assert voice_out is not None, "应带单条 voice"
    assert voice_out["content_id"] == str(voice.id)
    assert voice_out["duration"] == "0:05", f"duration 应格式化为 0:05: {voice_out['duration']!r}"
    # teardown：db_user 统一清理链覆盖 events/event_items/contents（按 user_id）


def test_timeline_voice_duration_none_when_absent(db_user):
    """A6：内容 duration 列为空 → voice.duration 下发 None（客户端显示空）"""
    from datetime import datetime, timezone

    from app.core.security import create_access_token
    from app.db.models import Event, EventItem
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    now = datetime.now(timezone.utc)
    event = Event(user_id=user.id, level=1, title="A6空", start_time=now, status="confirmed")
    db.add(event)
    voice = Content(
        id=str(uuid.uuid4()), user_id=user.id, content_type="voice",
        text=None, duration=None, status="done", source="app", taken_at=now,
    )
    db.add(voice)
    db.commit()
    db.add(EventItem(event_id=event.id, content_id=voice.id))
    db.commit()

    client = TestClient(app)
    headers = {"Authorization": f"Bearer {create_access_token(str(user.id))}"}
    r = client.get("/api/v1/events/timeline", headers=headers)
    assert r.status_code == 200, r.text
    target = next((e for e in r.json()["data"] if e["id"] == str(event.id)), None)
    assert target is not None
    assert target["voice"]["duration"] is None, "无时长应 None"
    assert target["voice"]["title"] == "语音记忆", "无文本应回退语音记忆"
    # teardown：db_user 统一清理链覆盖 events/event_items/contents（按 user_id）
