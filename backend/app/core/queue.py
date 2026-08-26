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


def enqueue_unique(
    func,
    key: str,
    *args,
    queue_name: str = QUEUE_HIGH,
    job_timeout: int = ASR_JOB_TIMEOUT,
    **kwargs,
):
    """job 级去重入队（F4/R5-4#5）：同 key 不重复入队

    确定性 job_id = "<func.__name__>_<key>" + Redis SETNX 原子预占位：
      - 首次：占位成功 → 入队，返回 job
      - 重复/并发同 key：占位失败 → 返回既有 job（不重复入队，防同一 content
        双任务双 ASR/embedding 计费、双聚合竞态、情绪增强双跑双副作用）
      - 既有 job 已 failed / 已过期 → 重建（RQ 同 job_id 覆盖）
    幂等键 TTL 7 天（与 RETRY_FAILURE_TTL 对齐，防键无限膨胀）。
    queue_name/job_timeout 可覆盖（情绪增强/文本类低优任务传 QUEUE_LOW + 300）。
    注意：job_id 用下划线拼接（RQ 2.x validate_job_id 只允许字母/数字/下划线/连字符，
    冒号会 ValueError——enqueue_idempotent 的冒号 job_id 存在同一潜在问题，待归口处理）。
    """
    name = getattr(func, "__name__", None)
    if name is None:
        name = func if isinstance(func, str) else type(func).__name__
    job_id = f"{name}_{key}"
    idem_key = f"yishu:uq:{job_id}"

    def _enqueue() -> object:
        return get_queue(queue_name).enqueue(
            func,
            *args,
            job_id=job_id,
            job_timeout=job_timeout,
            retry=RETRY_POLICY,
            failure_ttl=RETRY_FAILURE_TTL,
            **kwargs,
        )

    if redis.set(idem_key, "1", nx=True, ex=RETRY_FAILURE_TTL):
        return _enqueue()
    existing = get_job(job_id)
    if existing is not None and existing.get_status() != "failed":
        return existing
    return _enqueue()


def enqueue_idempotent(
    prefix: str,
    user_id: str,
    client_request_id: str,
    func,
    *args,
    job_timeout: int = ASR_JOB_TIMEOUT,
    **kwargs,
):
    """幂等入队（R4#4：classify/corrections 提交端点重试安全）

    确定性 job_id = "{prefix}:{user_id}:{client_request_id}" + Redis 原子预占位：
      - 首次提交：占位成功 → 入队，返回 job
      - 重复/并发提交：占位失败 → 返回既有 job（不重复入队，防双入队双执行）；
        既有 job 已失败/过期 → 重建（RQ 同 job_id 覆盖）。
    幂等键 TTL 7 天（与 RETRY_FAILURE_TTL 对齐，防键无限膨胀）。
    """
    job_id = f"{prefix}:{user_id}:{client_request_id}"
    idem_key = f"yishu:idem:{job_id}"

    def _enqueue() -> object:
        return get_queue(QUEUE_HIGH).enqueue(
            func,
            *args,
            job_id=job_id,
            job_timeout=job_timeout,
            retry=RETRY_POLICY,
            failure_ttl=RETRY_FAILURE_TTL,
            **kwargs,
        )

    if redis.set(idem_key, "1", nx=True, ex=RETRY_FAILURE_TTL):
        return _enqueue()
    existing = get_job(job_id)
    if existing is not None and existing.get_status() != "failed":
        return existing
    return _enqueue()
