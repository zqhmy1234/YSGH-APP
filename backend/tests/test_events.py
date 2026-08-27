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
from datetime import datetime, timedelta, timezone

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
    # 恰一次聚合入队，user 级 key + 参数透传
    assert len(calls) == 1, f"text 无情绪任务，仅应有一次聚合入队，实际 {calls}"
    func, key, a, kw = calls[0]
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
# US-12 时间存疑字段（Wave1-B2）：time_suspect 输出层最小校验
# ---------------------------------------------------------------------------


def _mk_event(db, user_id: str, start_time=None, title="测试事件", status="confirmed"):
    from app.db.models import Event

    ev = Event(
        id=str(uuid.uuid4()),
        user_id=user_id,
        level=1,
        title=title,
        start_time=start_time or datetime.now(timezone.utc),
        status=status,
        generated_by="cloud",
        confidence=0.9,
    )
    db.add(ev)
    db.commit()
    return ev


def _mk_photo(db, user_id: str, taken_at):
    from app.db.models import Content as _C

    c = _C(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo",
        taken_at=taken_at,
        status="done",
        source="app",
    )
    db.add(c)
    db.commit()
    return c


def _link(db, event_id: str, content_id: str) -> None:
    from app.db.models import EventItem

    db.add(EventItem(event_id=event_id, content_id=content_id))
    db.commit()


def _timeline_suspects(db, user) -> dict:
    """调 /events/timeline → {event_id: time_suspect}"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    try:
        r = client.get("/api/v1/events/timeline")
        assert r.status_code == 200, r.text
        return {e["id"]: e["time_suspect"] for e in r.json()["data"]}
    finally:
        app.dependency_overrides.clear()


def test_time_suspect_false_for_consistent_photo(db_user):
    """照片时间正常（拍摄早于导入、差异小）→ time_suspect=False"""
    db, user = db_user
    now = datetime.now(timezone.utc)
    ev = _mk_event(db, user.id, start_time=now - timedelta(hours=1))
    photo = _mk_photo(db, user.id, taken_at=now - timedelta(hours=2))
    _link(db, ev.id, photo.id)
    suspects = _timeline_suspects(db, user)
    assert suspects.get(str(ev.id)) is False


def test_time_suspect_true_for_future_dated_photo(db_user):
    """照片拍摄时间晚于导入时间超过阈值（96h>72h，未来时间戳/相机时钟错）→ time_suspect=True"""
    db, user = db_user
    now = datetime.now(timezone.utc)
    ev = _mk_event(db, user.id, start_time=now + timedelta(days=4))
    photo = _mk_photo(db, user.id, taken_at=now + timedelta(days=4))
    _link(db, ev.id, photo.id)
    suspects = _timeline_suspects(db, user)
    assert suspects.get(str(ev.id)) is True


def test_time_suspect_true_for_backward_drift_photo(db_user):
    """照片拍摄时间远早于导入时间（96h 差异）→ time_suspect=True（对称差异>阈值）"""
    db, user = db_user
    now = datetime.now(timezone.utc)
    ev = _mk_event(db, user.id, start_time=now - timedelta(days=4))
    photo = _mk_photo(db, user.id, taken_at=now - timedelta(days=4))
    _link(db, ev.id, photo.id)
    suspects = _timeline_suspects(db, user)
    assert suspects.get(str(ev.id)) is True


def test_time_suspect_false_when_taken_at_missing(db_user):
    """照片 taken_at 缺失（无 EXIF/无时间）→ 不误标，time_suspect=False（v2）"""
    db, user = db_user
    now = datetime.now(timezone.utc)
    ev = _mk_event(db, user.id, start_time=now)
    photo = _mk_photo(db, user.id, taken_at=None)
    _link(db, ev.id, photo.id)
    suspects = _timeline_suspects(db, user)
    assert suspects.get(str(ev.id)) is False


def test_time_suspect_batch_multiple_events(db_user):
    """批量：同一 timeline 请求多事件各取各的存疑标记（一次查询，互不干扰）"""
    db, user = db_user
    now = datetime.now(timezone.utc)
    ev_ok = _mk_event(db, user.id, start_time=now - timedelta(hours=2), title="正常")
    photo_ok = _mk_photo(db, user.id, taken_at=now - timedelta(hours=3))
    _link(db, ev_ok.id, photo_ok.id)
    ev_sus = _mk_event(db, user.id, start_time=now + timedelta(days=4), title="存疑")
    photo_sus = _mk_photo(db, user.id, taken_at=now + timedelta(days=4))
    _link(db, ev_sus.id, photo_sus.id)
    suspects = _timeline_suspects(db, user)
    assert suspects.get(str(ev_ok.id)) is False
    assert suspects.get(str(ev_sus.id)) is True


def test_time_suspect_false_for_text_only_event_legacy(db_user):
    """旧数据兼容：纯文字事件（无照片成员，旧数据无 time_suspect 概念）
    → 字段存在且 False（客户端 FIELD_TIME_SUSPECT 读取安全，不显示角标）"""
    db, user = db_user
    now = datetime.now(timezone.utc)
    ev = _mk_event(db, user.id, start_time=now - timedelta(hours=3))
    txt = Content(
        id=str(uuid.uuid4()), user_id=user.id, content_type="text",
        text="今天记点事", taken_at=now - timedelta(hours=3),
        status="done", source="app",
    )
    db.add(txt)
    db.commit()
    _link(db, ev.id, txt.id)
    # 字段存在性由 _timeline_suspects 内 e["time_suspect"] 保证（缺失会 KeyError 挂测试）
    suspects = _timeline_suspects(db, user)
    assert suspects.get(str(ev.id)) is False


def test_time_suspect_in_single_event_response(db_user):
    """单事件响应（confirm）同样带 time_suspect（详情/操作输出填充）"""
    from app.api import deps
    from app.main import app
    from fastapi.testclient import TestClient

    db, user = db_user
    now = datetime.now(timezone.utc)
    ev = _mk_event(db, user.id, start_time=now + timedelta(days=4), status="draft")
    photo = _mk_photo(db, user.id, taken_at=now + timedelta(days=4))
    _link(db, ev.id, photo.id)
    client = TestClient(app)
    app.dependency_overrides[deps.get_current_user] = lambda: user
    try:
        r = client.post("/api/v1/events/confirm", json={"event_id": str(ev.id)})
        assert r.status_code == 200, r.text
        assert r.json()["data"]["time_suspect"] is True
    finally:
        app.dependency_overrides.clear()
