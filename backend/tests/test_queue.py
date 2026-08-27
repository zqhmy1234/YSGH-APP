"""RQ 队列集成测试（决策 #9：API 入队 → worker 消费；API-016/017 语义）

前置：Docker Redis 容器 yishu-redis 运行中（AOF 持久化）
运行：pytest backend/tests/test_queue.py -v

H3（2026-08-27）：原 test_techdebt_p0.py 的 P0-8 入队契约测试按域迁入
（enqueue_high/low job_timeout/retry 参数、队列名）——纯单测，不依赖真实 Redis。
"""


import pytest
from app.core.config import settings
from redis import Redis


# ---------- H3：P0-8 RQ 入队契约（原 test_techdebt_p0.py 按域迁入） ----------


class _FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, func, *args, **kwargs):
        self.calls.append({"func": func, "args": args, "kwargs": kwargs})
        return "job-abc"


@pytest.fixture()
def fake_queue(monkeypatch):
    import app.core.queue as queue_mod

    fake = _FakeQueue()
    monkeypatch.setattr(queue_mod, "get_queue", lambda name: fake)
    return queue_mod, fake


def test_enqueue_high_defaults(fake_queue):
    """P0-8：高优队列默认 ASR 级超时 600s + 3 次指数退避 + failure_ttl"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_high(lambda: None, "arg")
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs["job_timeout"] == 600
    assert kwargs["retry"].max == 3
    assert kwargs["retry"].intervals == [10, 30, 90]
    assert kwargs["failure_ttl"] > 0
    assert kwargs["failure_ttl"] >= kwargs["job_timeout"]


def test_enqueue_low_defaults(fake_queue):
    """P0-8：低优队列默认 300s（聚合/批量非长任务）"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_low(lambda: None)
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs["job_timeout"] == 300
    assert kwargs["retry"].max == 3


def test_enqueue_job_timeout_override(fake_queue):
    """P0-8：调用方可覆盖 job_timeout（不破坏既有位置参数调用）"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_low(lambda: None, 1, 2, job_timeout=120)
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs["job_timeout"] == 120
    assert fake.calls[0]["args"] == (1, 2)


def test_enqueue_queue_names(fake_queue):
    """P0-8：high/low 队列名不变（worker 侧契约）"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_high(lambda: None)
    queue_mod.enqueue_low(lambda: None)
    assert [c["func"] for c in fake.calls]  # 两个都入队成功


@pytest.fixture(scope="module")
def redis_client():
    from urllib.parse import urlsplit, urlunsplit

    # R8#3：默认 redis_url 指向共享 /0 库——flushdb() 会清空他人/生产 RQ/缓存数据
    # （破坏性，与 P1C 已解决的 Qdrant collection 隔离同类）。改连独立测试库 /15：
    # flushdb 只清本文件写入的 key，不影响共享 Redis。
    parts = urlsplit(settings.redis_url)
    test_url = urlunsplit((parts.scheme, parts.netloc, "/15", parts.query, parts.fragment))
    r = Redis.from_url(test_url, decode_responses=False)  # RQ 需二进制连接
    r.flushdb()  # 只清独立测试库
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
