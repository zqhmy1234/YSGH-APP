"""受控失败记录经维护入口重投后，由真实 Redis/RQ worker 消费完成。

外部 AI、索引和后置队列增强用替身隔离；入队/消费/主状态提交走真实链路。
"""
import json
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import pytest
from app.core.config import settings
from app.db.models import Content
from app.services import pipeline
from app.workers import maintenance, requeue_job
from redis import Redis
from rq import Queue, SimpleWorker
from sqlalchemy import select, update

pytestmark = pytest.mark.integration


def test_requeue_is_consumed_by_worker(db_user, monkeypatch, capsys):
    db, user = db_user
    content = Content(user_id=user.id, content_type="text", source="app", status="failed",
                      text="维护任务测试", extra={"error": {"retryable": True, "code": "NETWORK_ERROR"}})
    db.add(content)
    db.flush()
    cid = str(content.id)
    db.execute(update(Content).where(Content.id == cid).values(updated_at=datetime.now(UTC) - timedelta(hours=2)))
    db.commit()
    # 保留真正 SQL 超龄谓词，只把候选范围限制为本测试记录。
    def candidates(session, cutoff, limit):
        return list(session.scalars(select(Content).where(
            Content.id == cid, Content.status.in_(("failed", "processing")),
            Content.updated_at <= cutoff, Content.deleted_at.is_(None)).limit(limit)))

    monkeypatch.setattr(requeue_job, "_stale_candidates", candidates)
    parts = urlsplit(settings.redis_url)
    redis = Redis.from_url(urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment)))
    queue = Queue(f"step2-{uuid.uuid4().hex}", connection=redis)
    jobs = []

    def enqueue(func, *args):
        job = queue.enqueue(func, *args)
        jobs.append(job)
        return job

    monkeypatch.setattr(requeue_job, "enqueue_high", enqueue)
    monkeypatch.setattr(pipeline, "_classify_content", lambda *a: None)
    for hook in ("annotate_on_ingest", "enrich_place_on_ingest", "mark_sensitive_on_ingest"):
        monkeypatch.setattr(f"app.services.pipeline_ext.{hook}", lambda *a: None)
    monkeypatch.setattr(pipeline, "_index_after_commit", lambda *a: None)
    monkeypatch.setattr(pipeline, "_enqueue_user_aggregation", lambda *a: "queued")
    monkeypatch.setattr("app.core.queue.enqueue_unique", lambda *a, **kw: None)
    try:
        assert maintenance.main(["requeue", "--dry-run"]) == 0
        assert json.loads(capsys.readouterr().out)["stats"]["requeued"] == 1
        assert queue.count == 0
        db.refresh(content)
        assert content.status == "failed" and "requeue_count" not in content.extra
        assert maintenance.main(["requeue"]) == 0
        assert json.loads(capsys.readouterr().out)["stats"]["requeued"] == 1
        assert queue.count == 1
        SimpleWorker([queue], connection=redis).work(burst=True)
        assert jobs[0].get_status(refresh=True) == "finished"
        db.refresh(content)
        assert content.status == "done"
        assert content.extra["requeue_count"] == 1
        assert "error" not in content.extra
        assert maintenance.main(["requeue"]) == 0
        assert json.loads(capsys.readouterr().out.splitlines()[-1])["stats"]["scanned"] == 0
    finally:
        for job in jobs:
            job.delete()
        queue.delete(delete_jobs=True)
        redis.close()
