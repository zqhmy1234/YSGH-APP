"""F-Events（F3 聚合独立 per-user 任务 + F5 events.py 拆包）测试

锚定：
  - run_user_aggregation：独立聚合 RQ 任务（自开 Session 跑 full 管线 → L1 落库）
  - per-user 去重：enqueue_unique 按 user 级 key SETNX 原子预占位——同用户并发多
    内容只入队一次聚合，不同用户各自入队（F3/R5-3 合并语义）
  - process_content 契约：主提交后按 user 级 key 入队聚合（不再同步跑聚合）
  - RQ 任务可解析：run_user_aggregation 模块路径 + events 包重导出 + worker 登记

前置：PG yishu 库（db_user 公共 fixture；纯 mock Redis 的去重用例不需要 DB）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _content(db, user_id: str, ts: datetime | None = None) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo",
        taken_at=ts or (datetime.now(timezone.utc) - timedelta(hours=1)),
        status="done",
        source="app",
    )
    db.add(c)
    db.commit()
    return c


# ---------------------------------------------------------------------------
# F3/R5-3：独立聚合任务（RQ worker 执行，自开 Session）
# ---------------------------------------------------------------------------


def test_run_user_aggregation_task_aggregates(db_user):
    """F3：run_user_aggregation 独立任务跑通 full 管线 → L1 日卡片落库"""
    from app.services.events import run_user_aggregation

    db, user = db_user
    base = datetime.now(timezone.utc) - timedelta(hours=3)
    _content(db, user.id, ts=base)
    _content(db, user.id, ts=base + timedelta(minutes=10))

    r = run_user_aggregation(str(user.id), mode="full")
    assert r.get("l1") >= 1, f"full 管线应产生 L1 日卡片，实际 {r}"
    ev = db.execute(
        select(Event).where(Event.user_id == user.id, Event.level == 1)
    ).scalars().first()
    assert ev is not None and ev.generated_by == "cloud"


def test_run_user_aggregation_silent_on_failure(db_user, monkeypatch):
    """F3：聚合任务失败静默——异常不冒泡，返回 error dict（用户无感知）"""
    from app.services import events as events_pkg
    from app.services.events import run_user_aggregation

    db, user = db_user
    _content(db, user.id)

    def boom(db_, user_id, **kwargs):
        raise RuntimeError("聚合爆炸")

    monkeypatch.setattr(events_pkg.aggregate, "aggregate_user", boom)
    r = run_user_aggregation(str(user.id))
    assert "error" in r
    # 任务自开 Session 已关闭（不抛异常即代表 finally 正常执行）
    assert r["error"]


# ---------------------------------------------------------------------------
# F3/R5-3：per-user 去重合并（enqueue_unique 按 user 级 key SETNX）
# ---------------------------------------------------------------------------


class _JobLike:
    """RQ Job 测试替身：get_status 返回指定状态"""

    def __init__(self, status: str = "queued"):
        self._status = status

    def get_status(self):
        return self._status


def _fake_queue_tooling(monkeypatch, enqueued: list[str]) -> None:
    import app.core.queue as queue_mod

    class FakeQueue:
        def enqueue(self, func, *args, **kwargs):
            enqueued.append(kwargs["job_id"])
            return {"job_id": kwargs["job_id"]}

    monkeypatch.setattr(queue_mod, "get_queue", lambda name: FakeQueue())


def test_enqueue_unique_per_user_dedup(monkeypatch):
    """F3：同用户并发多内容 → 聚合只入队一次（user 级 key）；不同用户各自入队"""
    import app.core.queue as queue_mod
    from app.services.events import run_user_aggregation

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    seen: set[str] = set()

    def fake_set(k, v, **kw):
        if kw.get("nx"):
            if k in seen:
                return False
            seen.add(k)
            return True
        return True

    monkeypatch.setattr(queue_mod.redis, "set", fake_set)
    monkeypatch.setattr(queue_mod, "get_job", lambda jid: _JobLike("queued"))

    # 同用户 u1 两次触发（模拟并发多内容）→ SETNX 第二次占位失败 → 只入队一次
    queue_mod.enqueue_unique(run_user_aggregation, "user:u1", "u1", mode="l2l3", queue_name="low", job_timeout=300)
    queue_mod.enqueue_unique(run_user_aggregation, "user:u1", "u1", mode="l2l3", queue_name="low", job_timeout=300)
    assert len(enqueued) == 1, "同用户并发多内容只应入队一次聚合"
    assert enqueued[0] == "run_user_aggregation_user_u1"

    # 不同用户 u2 → 各自入队（互不覆盖）
    queue_mod.enqueue_unique(run_user_aggregation, "user:u2", "u2", mode="l2l3", queue_name="low", job_timeout=300)
    assert enqueued == ["run_user_aggregation_user_u1", "run_user_aggregation_user_u2"]


def test_enqueue_unique_user_key_sanitized(monkeypatch):
    """F3：user 级 key 含非法字符 → job_id 净化（不触发 RQ validate_job_id ValueError）"""
    import app.core.queue as queue_mod
    from app.services.events import run_user_aggregation

    enqueued: list[str] = []
    _fake_queue_tooling(monkeypatch, enqueued)
    monkeypatch.setattr(queue_mod.redis, "set", lambda k, v, **kw: True)

    queue_mod.enqueue_unique(run_user_aggregation, "user:uuid-1234", "uuid-1234")
    assert len(enqueued) == 1
    assert all(c.isalnum() or c in "_-" for c in enqueued[0])
    assert enqueued[0].startswith("run_user_aggregation_user_uuid")


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
