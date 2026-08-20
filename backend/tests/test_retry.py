"""外部 API 统一重试封装测试（2026-08-20 · 生产兜底 P0）

覆盖：
  - 网络抖动（ConnectionError）→ 重试后成功
  - 重试耗尽 → RetryExhaustedError
  - 业务错误（ValueError）→ 不重试直接抛
  - 5xx 错误消息 → 判定可重试
  - timeout 参数生效
"""
import time

import pytest
from app.services.external.retry import RetryExhaustedError, _is_retryable, with_retry


class TestRetryableDetection:
    def test_connection_error_retryable(self):
        assert _is_retryable(ConnectionError("10053"))

    def test_timeout_error_retryable(self):
        assert _is_retryable(TimeoutError())

    def test_5xx_in_message_retryable(self):
        assert _is_retryable(RuntimeError("dashscope qwen-flash 调用失败: 500 internal"))

    def test_business_error_not_retryable(self):
        assert not _is_retryable(ValueError("参数错误"))


class TestWithRetry:
    def test_success_first_try(self):
        calls = []

        @with_retry(retries=3)
        def ok():
            calls.append(1)
            return "done"

        assert ok() == "done"
        assert len(calls) == 1  # 一次成功不重试

    def test_recovers_after_transient_failure(self):
        calls = []

        @with_retry(retries=3, backoff=(0.01, 0.01))
        def flaky():
            calls.append(1)
            if len(calls) < 2:
                raise ConnectionError("10053 网络中断")
            return "ok"

        assert flaky() == "ok"
        assert len(calls) == 2  # 失败一次后重试成功

    def test_exhausted_raises(self):
        calls = []

        @with_retry(retries=3, backoff=(0.01, 0.01))
        def always_fail():
            calls.append(1)
            raise ConnectionError("永远失败")

        with pytest.raises(RetryExhaustedError):
            always_fail()
        assert len(calls) == 3  # 恰好尝试 3 次

    def test_business_error_no_retry(self):
        calls = []

        @with_retry(retries=3)
        def bad():
            calls.append(1)
            raise ValueError("业务错误")

        with pytest.raises(ValueError):
            bad()
        assert len(calls) == 1  # 业务错误不重试

    def test_wraps_metadata(self):
        @with_retry()
        def my_func():
            """文档字符串"""
            return 1

        assert my_func.__name__ == "my_func"
        assert my_func.__doc__ == "文档字符串"

    def test_timeout_parameter(self):
        @with_retry(retries=1, timeout=1)
        def slow():
            time.sleep(3)
            return "too late"

        t0 = time.perf_counter()
        with pytest.raises(RetryExhaustedError):
            slow()
        assert time.perf_counter() - t0 < 3  # 1s 超时触发，没等 3s
