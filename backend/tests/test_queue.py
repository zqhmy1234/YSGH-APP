"""RQ 队列集成测试（决策 #9：API 入队 → worker 消费；API-016/017 语义）

前置：Docker Redis 容器 yishu-redis 运行中（AOF 持久化）
运行：pytest backend/tests/test_queue.py -v
"""


import pytest
from app.core.config import settings
from redis import Redis


@pytest.fixture(scope="module")
def redis_client():
    r = Redis.from_url(settings.redis_url, decode_responses=False)  # RQ 需二进制连接
    r.flushdb()
    yield r
    r.flushdb()


@pytest.mark.integration
def test_redis_connection(redis_client):
    assert redis_client.ping()


@pytest.mark.integration
def test_enqueue_and_worker_consume(redis_client):
    """入队 process_content → 起 worker 消费 → 任务完成"""
    from app.services.pipeline import process_content
    from app.workers.worker import get_worker_class
    from rq import Queue

    queue = Queue("high", connection=redis_client)
    job = queue.enqueue(process_content, "content-001")
    assert job.id

    worker = get_worker_class()([queue], connection=redis_client)
    worker.work(burst=True)  # 消费一次即停

    refreshed = queue.fetch_job(job.id)
    assert refreshed is not None
    # SimpleWorker（Windows 无 fork）同步执行路径不落 return_value，以 job 状态为准
    assert refreshed.get_status() == "finished"


@pytest.mark.integration
def test_queue_failure_goes_to_dead(redis_client):
    """任务抛错 → 失败状态可查（API-017：重试耗尽入死信）"""
    from app.workers.worker import get_worker_class
    from rq import Queue

    queue = Queue("low", connection=redis_client)

    def boom():
        raise ValueError("boom")

    job = queue.enqueue(boom)
    worker = get_worker_class()([queue], connection=redis_client)
    worker.work(burst=True)
    refreshed = queue.fetch_job(job.id)
    assert refreshed.is_failed
    # RQ 2.x：exc_info 已弃用，改走 latest_result()（兼容 1.x 属性）
    result = refreshed.latest_result()
    exc_str = result.exc_string if result is not None else getattr(refreshed, "exc_info", "")
    assert "boom" in (exc_str or "")
