"""RQ Worker 进程入口（P2-02 重构：内容 AI 管线已下沉 services/pipeline.py）

职责：仅进程启动入口（Windows SimpleWorker / Linux Worker 选择）与 RQ 任务注册。

启动命令：
  python -m app.workers.worker [queue...]
（默认队列：high,low——high=P0/P1 语音/照片，low=P2-P4 聚合/批量）

任务函数路径：app.services.pipeline.process_content（RQ pickle 依赖模块路径，
存量队列中旧路径 job 需在低峰期清空/重投，见 refactor-plan P2-02 风险控制）。

A2（P0-2）：启动 with_scheduler=True——RQ Retry 重投的任务先进入
ScheduledJobRegistry，只有 RQ Scheduler 会把到期任务搬回队列；不起 scheduler
则 retry 永不真正重投（配合 pipeline 的 re-raise 语义，缺一不可）。多 worker
部署时按队列 Redis 锁互斥，仅一个进程持有 scheduler 锁，其余自动跳过。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("yishu.worker")


def get_worker_class():
    """Windows 用 SimpleWorker（无 fork），Linux 用默认 Worker"""
    import platform

    if platform.system() == "Windows":
        from rq.worker import SimpleWorker

        return SimpleWorker
    from rq import Worker

    return Worker


def main() -> None:
    """启动 RQ worker（python -m app.workers.worker [queue...]）"""
    import sys

    from app.core.queue import QUEUE_HIGH, QUEUE_LOW, get_queue, redis

    queues = sys.argv[1:] or [QUEUE_HIGH, QUEUE_LOW]
    # 预导入任务函数（RQ 从模块路径反序列化，确保可导入）
    from app.services import pipeline  # noqa: F401  —— 注册 process_content
    from app.services.events import run_user_aggregation  # noqa: F401  —— 注册聚合任务（F3）

    worker = get_worker_class()(
        [get_queue(q) for q in queues],
        connection=redis,
    )
    logger.info("RQ worker 启动，监听队列: %s", queues)
    # A2（P0-2）：with_scheduler=True —— Retry 重投任务先进 ScheduledJobRegistry，
    # 需 RQ Scheduler 搬回队列才真正重投（此前吞异常 + 无 scheduler = 双重失效）。
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
