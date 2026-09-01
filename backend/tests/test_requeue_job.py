"""超龄 failed/processing 内容重扫 job 测试（A2 · P0-2 RQ 重试语义修正配套）

覆盖：
  - failed + retryable=True 超龄 → 重新入队 process_content（requeue_count+1）
  - failed + retryable=False 超龄 → 终态跳过（不重投）
  - processing 超龄（worker 崩溃遗留）→ 重新入队
  - 重扫计数达上限 → 置终态（retryable=False + requeue_exhausted，保留原错误码）
  - 真实 SQL 路径：未超龄内容不选中（limit=1 时只选最旧超龄内容）
  - dry_run：只计数不落库不重投
前置：PG yishu 库；入队走 monkeypatch（不碰真实 Redis）
"""
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from app.db.models import Content, User
from app.db.session import SessionLocal
from app.services.pipeline import process_content
from app.workers.requeue_job import REQUEUE_MAX_ATTEMPTS, run_requeue
from sqlalchemy import delete as sa_delete
from sqlalchemy import update as sa_update

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"requeue-test-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


def _content(
    db,
    user_id: str,
    *,
    ctype: str = "text",
    status: str = "failed",
    extra: dict | None = None,
) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type=ctype,
        status=status,
        source="app",
        extra=extra,
    )
    db.add(c)
    db.commit()
    return c


def _age(db, content: Content, hours: float) -> None:
    """把 updated_at 拨到 N 小时前（Core update 绕过 ORM onupdate，精确落值）"""
    db.execute(
        sa_update(Content)
        .where(Content.id == content.id)
        .values(updated_at=datetime.now(timezone.utc) - timedelta(hours=hours))
    )
    db.commit()
    db.refresh(content)


def _collector(monkeypatch):
    """monkeypatch enqueue_high 收集入队调用（不碰真实 Redis）"""
    calls = []
    monkeypatch.setattr(
        "app.workers.requeue_job.enqueue_high",
        lambda func, content_id: calls.append((func, content_id)),
    )
    return calls


def _pin_candidates(monkeypatch, content):
    """把候选扫描钉死为单条内容（行为测试确定性，隔离共享库历史脏数据）。

    必须用 job 自身会话重新加载（db_.get）——直接返回测试会话持有的实例时，
    job 内 db.commit() 不追踪该对象，计数/置终态改动不会落库。
    """
    monkeypatch.setattr(
        "app.workers.requeue_job._stale_candidates",
        lambda db_, cutoff, limit: [db_.get(Content, str(content.id))],
    )


def test_failed_retryable_stale_is_requeued(db_user, monkeypatch):
    """failed + retryable=True 超龄 → 重新入队 process_content（计数 +1）"""
    db, user = db_user
    c = _content(
        db,
        user.id,
        ctype="voice",
        extra={"error": {"code": "NETWORK_ERROR", "retryable": True}},
    )
    _age(db, c, 2)
    calls = _collector(monkeypatch)
    _pin_candidates(monkeypatch, c)

    report = run_requeue()

    assert report["scanned"] == 1
    assert report["requeued"] == 1
    assert calls == [(process_content, str(c.id))]
    db.refresh(c)
    assert (c.extra or {}).get("requeue_count") == 1


def test_failed_final_is_skipped(db_user, monkeypatch):
    """failed + retryable=False（终态，如 AUDIO_NOT_FOUND）→ 不重投"""
    db, user = db_user
    c = _content(
        db,
        user.id,
        extra={"error": {"code": "AUDIO_NOT_FOUND", "retryable": False}},
    )
    _age(db, c, 2)
    calls = _collector(monkeypatch)
    _pin_candidates(monkeypatch, c)

    report = run_requeue()

    assert report["scanned"] == 1
    assert report["skipped_final"] == 1
    assert report["requeued"] == 0
    assert calls == []
    db.refresh(c)
    assert "requeue_count" not in (c.extra or {})
    assert c.status == "failed"


def test_stale_processing_is_requeued(db_user, monkeypatch):
    """processing 超龄（worker 崩溃遗留）→ 重新入队"""
    db, user = db_user
    c = _content(db, user.id, ctype="text", status="processing")
    _age(db, c, 3)
    calls = _collector(monkeypatch)
    _pin_candidates(monkeypatch, c)

    report = run_requeue()

    assert report["requeued"] == 1
    assert calls == [(process_content, str(c.id))]
    db.refresh(c)
    assert (c.extra or {}).get("requeue_count") == 1


def test_requeue_exhausted_is_finalized(db_user, monkeypatch):
    """重扫计数达上限 → 置终态（retryable=False + requeue_exhausted），不再骚扰"""
    db, user = db_user
    c = _content(
        db,
        user.id,
        ctype="voice",
        extra={
            "requeue_count": REQUEUE_MAX_ATTEMPTS,
            "error": {"code": "NETWORK_ERROR", "retryable": True},
        },
    )
    _age(db, c, 2)
    calls = _collector(monkeypatch)
    _pin_candidates(monkeypatch, c)

    report = run_requeue()

    assert report["finalized"] == 1
    assert calls == []
    db.refresh(c)
    assert c.status == "failed"
    error = c.extra["error"]
    assert error["retryable"] is False
    assert error["requeue_exhausted"] is True
    assert error["code"] == "NETWORK_ERROR"  # 保留原错误码溯源
    assert c.extra["audio_processing"]["outcome"] == "failed_final"


def test_real_query_only_picks_stale(db_user, monkeypatch):
    """真实 SQL 路径：未超龄内容不选中；limit=1 只选中最旧超龄内容"""
    db, user = db_user
    fresh = _content(
        db, user.id, extra={"error": {"code": "NETWORK_ERROR", "retryable": True}}
    )
    stale = _content(
        db, user.id, extra={"error": {"code": "NETWORK_ERROR", "retryable": True}}
    )
    _age(db, stale, 2)
    # 拨到远超任何历史数据的最旧位置，保证 limit=1 必选它
    db.execute(
        sa_update(Content)
        .where(Content.id == stale.id)
        .values(updated_at=datetime(2000, 1, 1, tzinfo=timezone.utc))
    )
    db.commit()
    calls = _collector(monkeypatch)

    report = run_requeue(limit=1)

    assert report["requeued"] == 1
    assert calls == [(process_content, str(stale.id))]
    db.refresh(stale)
    assert (stale.extra or {}).get("requeue_count") == 1
    db.refresh(fresh)
    assert "requeue_count" not in (fresh.extra or {})


def test_dry_run_changes_nothing(db_user, monkeypatch):
    """dry_run：只扫描统计，不重投不落库"""
    db, user = db_user
    c = _content(
        db,
        user.id,
        ctype="voice",
        extra={"error": {"code": "NETWORK_ERROR", "retryable": True}},
    )
    _age(db, c, 2)
    calls = _collector(monkeypatch)
    _pin_candidates(monkeypatch, c)

    report = run_requeue(dry_run=True)

    assert report["requeued"] == 1
    assert calls == []
    db.refresh(c)
    assert "requeue_count" not in (c.extra or {})
    assert c.status == "failed"
