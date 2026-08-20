"""RQ 队列配置（决策 #9：API 只 enqueue 立即返回，worker 独立进程跑 CPU 推理）"""
from redis import Redis
from rq import Queue

from app.core.config import settings

# 主连接（AOF 持久化，备份 DR-004）
# 注意：RQ 的 job payload 是 pickle 二进制，连接必须 decode_responses=False
# （业务缓存若需字符串自动解码，另建一个 decode 连接）
redis = Redis.from_url(settings.redis_url, decode_responses=False)


def get_queue(name: str = "default", **kwargs) -> Queue:
    """获取命名队列（任务类型多后可按优先级分队列，B5-d-5）"""
    return Queue(name, connection=redis, **kwargs)


# 常用队列（B5-d-5 优先级映射）
QUEUE_HIGH = "high"      # P0/P1：语音转写、新照片上传（用户在等）
QUEUE_LOW = "low"        # P2-P4：聚合重跑、云侧拉取、批量导入


def enqueue_high(func, *args, **kwargs):
    """高优先级入队（语音转写/照片上传）"""
    return get_queue(QUEUE_HIGH).enqueue(func, *args, **kwargs)


def enqueue_low(func, *args, **kwargs):
    """低优先级入队（聚合/拉取/批量）"""
    return get_queue(QUEUE_LOW).enqueue(func, *args, **kwargs)


def get_job(job_id: str):
    """按 job_id 取 RQ Job（P2-01：异步推理任务查询）"""
    from rq.job import Job

    try:
        return Job.fetch(job_id, connection=redis)
    except Exception:  # noqa: BLE001 —— job 不存在/已过期
        return None
