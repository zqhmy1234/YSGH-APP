"""RQ 队列配置（决策 #9：API 只 enqueue 立即返回，worker 独立进程跑 CPU 推理）

P0-8（审查 S3-低/运维，2026-08-26）：显式超时 + 重试策略
  - job_timeout：ASR 类长任务（语音转写/照片管线）600s，其余默认 300s——
    防止 worker 挂死任务无超时上限（此前 RQ 默认 5 分钟，长转写可能被截断）
  - retry：3 次指数退避（10s → 30s → 90s）——网络抖动/瞬时故障自动重投
    （RQ Retry 由 worker 在失败时按 interval 重新入队；幂等任务重投安全）

部署建议（登记给集成 Agent）：生产按队列拆分 worker 进程——
  high 队列（语音转写/照片上传，用户在等）与 low 队列（聚合/批量）各起一个
  worker，避免批量任务饿死实时转写；低峰可加 low 队列并发 worker 数。
  示例（backend/ 下）：
      rq worker high   --url $REDIS_URL
      rq worker low    --url $REDIS_URL
"""
from redis import Redis
from rq import Queue, Retry

from app.core.config import settings

# 主连接（AOF 持久化，备份 DR-004）
# 注意：RQ 的 job payload 是 pickle 二进制，连接必须 decode_responses=False
# （业务缓存若需字符串自动解码，另建一个 decode 连接）
redis = Redis.from_url(settings.redis_url, decode_responses=False)

# 任务超时（秒）
DEFAULT_JOB_TIMEOUT = 300          # 默认：聚合/批量/文本类
ASR_JOB_TIMEOUT = 600              # ASR 类：语音转写/照片管线（长任务）
# 失败重试：3 次指数退避（10s → 30s → 90s；interval 列表即逐次等待）
RETRY_POLICY = Retry(max=3, interval=[10, 30, 90])
# 失败记录保留时长（重试窗口内 job 不可被清扫；7 天足够覆盖 3 次退避）
RETRY_FAILURE_TTL = 7 * 24 * 3600


def get_queue(name: str = "default", **kwargs) -> Queue:
    """获取命名队列（任务类型多后可按优先级分队列，B5-d-5）"""
    return Queue(name, connection=redis, **kwargs)


# 常用队列（B5-d-5 优先级映射）
QUEUE_HIGH = "high"      # P0/P1：语音转写、新照片上传（用户在等）
QUEUE_LOW = "low"        # P2-P4：聚合重跑、云侧拉取、批量导入


def enqueue_high(func, *args, job_timeout: int = ASR_JOB_TIMEOUT, retry=RETRY_POLICY, **kwargs):
    """高优先级入队（语音转写/照片上传）——默认 ASR 级超时 600s

    job_timeout/retry 可覆盖（如纯文本类任务可传 job_timeout=300）。
    """
    return get_queue(QUEUE_HIGH).enqueue(
        func,
        *args,
        job_timeout=job_timeout,
        retry=retry,
        failure_ttl=RETRY_FAILURE_TTL,
        **kwargs,
    )


def enqueue_low(func, *args, job_timeout: int = DEFAULT_JOB_TIMEOUT, retry=RETRY_POLICY, **kwargs):
    """低优先级入队（聚合/拉取/批量）——默认 300s"""
    return get_queue(QUEUE_LOW).enqueue(
        func,
        *args,
        job_timeout=job_timeout,
        retry=retry,
        failure_ttl=RETRY_FAILURE_TTL,
        **kwargs,
    )


def get_job(job_id: str):
    """按 job_id 取 RQ Job（P2-01：异步推理任务查询）"""
    from rq.job import Job

    try:
        return Job.fetch(job_id, connection=redis)
    except Exception:  # noqa: BLE001 —— job 不存在/已过期
        return None
