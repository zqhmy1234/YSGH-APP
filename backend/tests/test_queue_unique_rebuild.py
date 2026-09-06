"""R9-B3 回归：enqueue_unique 的 finished job 必须重建（不能被幂等键吞掉）

根因（2026-09-06 实锤）：旧判定 `existing.get_status() != "failed"` 把 finished
也当「已处理」直接返回——聚合 job 是 user 级确定性 job_id + 幂等键 TTL 7 天，
首次聚合 finished 后，同窗口内该用户后续每条新内容的聚合请求全被吞掉，
新内容永不进事件层（时间轴）→「搜索看得到、时间轴看不到」的终极根因之一。

本测试要求本机 Redis 可用（rq/redis 依赖 app.core.queue 的真实连接）。
"""
import uuid

import pytest

rq = pytest.importorskip("rq")

from app.core.queue import QUEUE_LOW, enqueue_unique, get_job, redis  # noqa: E402


def _noop_agg(user_id: str) -> dict:
    return {"user": user_id}


@pytest.fixture()
def _clean_keys():
    """前后清理本测试用到的幂等键与 job hash（确定性 job_id 由 key 派生）。"""
    keys_used = []

    def _register(key: str) -> str:
        keys_used.append(key)
        return key

    yield _register
    for key in keys_used:
        job_id = f"_noop_agg_{key}"
        redis.delete(f"yishu:uq:{job_id}")
        redis.delete(f"rq:job:{job_id}")


def _job_id(key: str) -> str:
    return f"_noop_agg_{key}"


class TestEnqueueUniqueFinishedRebuild:
    def test_first_enqueue_creates_job(self, _clean_keys):
        key = _clean_keys(f"r9b3_first_{uuid.uuid4().hex[:8]}")
        job = enqueue_unique(
            _noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60
        )
        assert job is not None
        assert job.get_status() in ("queued", "started", "finished")

    def test_inflight_job_is_deduplicated(self, _clean_keys):
        """在途（queued）job：同 key 第二次入队必须返回既有 job，不重复入队。"""
        key = _clean_keys(f"r9b3_inflight_{uuid.uuid4().hex[:8]}")
        job1 = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        job2 = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        assert job1.id == job2.id

    def test_finished_job_is_rebuilt(self, _clean_keys):
        """核心回归：finished job 不得吞掉新请求——必须重建重入队。

        场景还原：聚合 job 跑完（finished），同用户新内容触发第二次聚合，
        旧实现直接返回既有 finished job = 聚合永不重跑 = 时间轴永不见新内容。
        """
        key = _clean_keys(f"r9b3_finished_{uuid.uuid4().hex[:8]}")
        job1 = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        # 模拟 job 已完成（worker 消费后的终态）
        job1.set_status("finished")
        job1.save()
        assert get_job(_job_id(key)).get_status() == "finished"

        job2 = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        assert job2.get_status() in ("queued", "started"), (
            "finished job 必须触发重建重入队，而不是返回旧 finished job"
        )

    def test_failed_job_is_rebuilt(self, _clean_keys):
        """failed → 重建（原设计保留，防回归）。"""
        key = _clean_keys(f"r9b3_failed_{uuid.uuid4().hex[:8]}")
        job1 = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        job1.set_status("failed")
        job1.save()
        job2 = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        assert job2.get_status() in ("queued", "started")

    def test_idempotent_finished_rebuild_matches_unique(self, _clean_keys):
        """enqueue_idempotent 与 enqueue_unique 同款修复（同一缺陷两处代码）。"""
        from app.core.queue import enqueue_idempotent

        rid = uuid.uuid4().hex[:8]
        key = f"r9b3_idem_{rid}"
        _clean_keys(key)
        job1 = enqueue_idempotent(
            "testop", "user-x", rid, _noop_agg, "user-x", queue_name=QUEUE_LOW, job_timeout=60
        )
        job1.set_status("finished")
        job1.save()
        job2 = enqueue_idempotent(
            "testop", "user-x", rid, _noop_agg, "user-x", queue_name=QUEUE_LOW, job_timeout=60
        )
        assert job2.get_status() in ("queued", "started")


class TestEnqueueArgsPassthrough:
    """R9-B6 回归（2026-09-06 实锤）：enqueue_unique/enqueue_idempotent 的 key 只是
    去重键，函数参数必须经 *args 显式透传——process_content 等 8 处调用点此前只传
    key 没传 args，worker 零参调用 TypeError 秒死（failed:high 262 条同因），
    且 job hash 不落 exc_info，worker stderr 之外完全不可见。"""

    def test_function_args_are_passed_through(self, _clean_keys):
        key = _clean_keys(f"r9b6_args_{uuid.uuid4().hex[:8]}")
        job = enqueue_unique(_noop_agg, key, key, queue_name=QUEUE_LOW, job_timeout=60)
        # job.args 从 data 反序列化——空 args 会让 worker 抛 TypeError: missing argument
        assert job.args == (key,), f"函数参数未透传: args={job.args!r}"

    def test_idempotent_function_args_are_passed_through(self):
        from app.core.queue import enqueue_idempotent

        rid = uuid.uuid4().hex[:8]
        key = f"r9b6_idem_{rid}"  # noqa: F841
        job = enqueue_idempotent(
            "testop", "user-x", rid, _noop_agg, "user-x",
            queue_name=QUEUE_LOW, job_timeout=60,
        )
        assert job.args == ("user-x",), f"函数参数未透传: args={job.args!r}"
        redis.delete(f"rq:job:{job.id}")
