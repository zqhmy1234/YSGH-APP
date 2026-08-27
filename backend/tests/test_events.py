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
