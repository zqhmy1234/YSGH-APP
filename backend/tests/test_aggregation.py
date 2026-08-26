"""F3/R5-3 事件聚合独立 per-user 任务测试

锚定（对齐 docs/重构批次F提示词_20260827.md F-Events 验收）：
  - run_user_aggregation：独立聚合 RQ 任务（自开 Session 跑 full 管线 → L1 落库；
    聚合失败静默返回 error dict）
  - per-user 去重并发：core/queue.enqueue_unique 按 user 级 key（user:<uid>）SETNX
    原子预占位——同用户并发/重复触发只入队一次聚合（重复触发不重复算），
    不同用户各自入队；user key 净化不触发 RQ validate_job_id
  - _write_upper_candidates 幂等落 DB 层：候选已落库（成员已挂 level>=2）→
    跳过重查/重写（added=0，语义对齐 P1B 已落地的增量聚合）

前置：PG yishu 库（db_user 公共 fixture；纯 mock Redis 的去重用例不需要 DB）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, Event
from sqlalchemy import select

pytestmark = pytest.mark.integration


def _content(db, user_id: str, ts: datetime | None = None, tags: list[str] | None = None) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo",
        taken_at=ts or (datetime.now(timezone.utc) - timedelta(hours=1)),
        status="done",
        source="app",
        extra={"ci_tags": tags} if tags else None,
    )
    db.add(c)
    db.commit()
    return c


# ---------------------------------------------------------------------------
# run_user_aggregation 独立任务（RQ worker 执行，自开 Session）
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
    assert "error" in r and r["error"]


# ---------------------------------------------------------------------------
# per-user 去重合并（enqueue_unique 按 user 级 key SETNX；重复触发不重复算）
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
    assert len(enqueued) == 1, "同用户并发多内容只应入队一次聚合（重复触发不重复算）"
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
# _write_upper_candidates 幂等落 DB 层（候选存在即跳过重查/重写，对齐 P1B 增量聚合）
# ---------------------------------------------------------------------------


def test_write_upper_candidates_idempotent(db_user):
    """F3/P1B：同批 L2 候选二次落库 → 成员已挂 level>=2 → 跳过重写（added=0）"""
    from app.services.event_aggregation.pipeline import RawPhoto
    from app.services.events import _l2l3_candidates_from_photos, _write_upper_candidates

    db, user = db_user
    base = datetime(2026, 8, 1, 10, 0, tzinfo=timezone.utc)
    contents = [
        _content(db, user.id, ts=base + timedelta(days=d, minutes=i * 5), tags=["美食"])
        for d in range(2) for i in range(6)   # 跨 2 天 12 张同标签 → L2 候选
    ]
    photos = [RawPhoto(id=str(c.id), ts=c.taken_at, tags=c.extra["ci_tags"]) for c in contents]
    l2, l3 = _l2l3_candidates_from_photos(photos)
    assert l2, "12 张跨天同标签应形成 L2 候选"

    first = _write_upper_candidates(db, str(user.id), l2, l3)
    assert first >= 10, f"首轮应落库候选成员，实际 {first}"
    db.commit()
    count_after_first = len(
        db.execute(select(Event).where(Event.user_id == user.id, Event.level >= 2)).scalars().all()
    )

    second = _write_upper_candidates(db, str(user.id), l2, l3)
    assert second == 0, "候选已落库 → 幂等跳过，不重查/重写"
    db.commit()
    count_after_second = len(
        db.execute(select(Event).where(Event.user_id == user.id, Event.level >= 2)).scalars().all()
    )
    assert count_after_second == count_after_first, "幂等：重复触发不重复建事件"
