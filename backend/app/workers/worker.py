"""RQ Worker 入口（决策 #9：独立 worker 进程跑 CPU 密集推理）

启动：
  rq worker high low --url redis://localhost:6379/0
  （高/低两个队列，见 B5-d-5 优先级）

⚠️ Windows 注意：RQ 默认 Worker 用 os.fork()，Windows 不支持 → 用 SimpleWorker。
  生产（Linux）用默认 Worker。
"""
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("yishu.worker")


def get_worker_class():
    """Windows 用 SimpleWorker（无 fork），Linux 用默认 Worker"""
    import platform

    if platform.system() == "Windows":
        from rq.worker import SimpleWorker

        return SimpleWorker
    from rq import Worker

    return Worker


def process_content(content_id: str) -> dict:
    """内容处理任务（API-016 队列编排：收件→转写→分类→聚类）

    TODO(T1): M1 实现真实管线（ASR/SetFit/聚类），当前返回占位状态。
    """
    logger.info("process_content %s start", content_id)
    # TODO(T1): 异步 AI 管线（转写→分类→L0 聚类）→ contents.status 回写 done
    return {"content_id": content_id, "status": "done", "pipeline": "mock"}


def retry_job(func, *args, retries: int = 3, backoff: tuple = (2, 4, 8), **kwargs):
    """指数退避重试（API-017：抖动重试 3 次，耗尽入死信）"""
    import time

    for i, delay in enumerate(backoff[:retries]):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001 —— worker 边界统一处理
            logger.warning("retry %d/%d for %s: %s", i + 1, retries, func.__name__, exc)
            time.sleep(delay)
    raise RuntimeError(f"job failed after {retries} retries: {func.__name__}")
