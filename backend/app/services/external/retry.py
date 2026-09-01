"""外部 API 统一重试封装（2026-08-20 · 生产兜底 P0）

背景（AGENTS.md #13 教训）：批量外部调用无重试导致 121 张 ConnectionError（10053）
浪费两轮时间。统一封装：指数退避重试 + 可配次数 + 超时，覆盖所有外部 API 调用点。

用法：
  from app.services.external.retry import with_retry

  @with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
  def call_api(...):
      ...

设计：
- 重试仅对"可重试异常"生效：ConnectionError / TimeoutError / OSError / HTTP 5xx
- 业务错误（4xx/参数错）不重试（重试无意义）
- 装饰器保留原函数签名与文档（functools.wraps）
"""
from __future__ import annotations

import logging
import time
from functools import wraps

logger = logging.getLogger("yishu.external.retry")

# 可重试异常（网络层抖动/服务端 5xx）
_RETRYABLE_EXC = (ConnectionError, TimeoutError, OSError)

DEFAULT_RETRIES = 3
DEFAULT_BACKOFF = (1, 2, 4)  # 秒


class RetryExhaustedError(RuntimeError):
    """重试耗尽（区别于业务错误：调用方可据此降级/兜底）"""


def _is_retryable(exc: BaseException) -> bool:
    """判断异常是否可重试：网络层异常，或含 5xx 的错误消息"""
    if isinstance(exc, _RETRYABLE_EXC):
        return True
    msg = str(exc).lower()
    for code in ("500", "502", "503", "504", "10053", "10054", "timed out", "timeout"):
        if code in msg:
            return True
    return False


def with_retry(
    retries: int = DEFAULT_RETRIES,
    backoff: tuple = DEFAULT_BACKOFF,
    timeout: float | None = None,
):
    """指数退避重试装饰器

    retries：总尝试次数（含首次）
    backoff：重试间隔秒数（超出的按最后一个值）
    timeout：单次调用超时秒数（None = 不设，用 SDK 默认）
    """

    def deco(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delays = list(backoff)[: retries - 1]
            last_exc: BaseException | None = None
            for attempt in range(retries):
                try:
                    if timeout is not None:
                        import concurrent.futures as _cf

                        # 用线程池实现超时（SDK 调用多为阻塞 IO，无法中断）；
                        # shutdown(wait=False) 不等待超时线程（否则 sleep 拖垮调用）
                        pool = _cf.ThreadPoolExecutor(max_workers=1)
                        try:
                            future = pool.submit(func, *args, **kwargs)
                            try:
                                return future.result(timeout=timeout)
                            except _cf.TimeoutError:
                                future.cancel()
                                raise TimeoutError(f"{func.__name__} 调用超时 {timeout}s") from None
                        finally:
                            pool.shutdown(wait=False, cancel_futures=True)
                    return func(*args, **kwargs)
                except Exception as exc:  # noqa: BLE001 —— 重试判定看类型
                    last_exc = exc
                    if not _is_retryable(exc):
                        raise
                    if attempt < retries - 1:
                        delay = delays[attempt] if attempt < len(delays) else delays[-1]
                        logger.warning(
                            "外部调用 %s 第 %d/%d 次失败（%s），%ss 后重试",
                            func.__name__, attempt + 1, retries, exc, delay,
                        )
                        time.sleep(delay)
            raise RetryExhaustedError(
                f"{func.__name__} 重试 {retries} 次仍失败: {last_exc}"
            ) from last_exc

        return wrapper

    return deco
