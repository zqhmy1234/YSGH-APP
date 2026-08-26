"""测试轮询工具（R8#9 2026-08-27：固定 sleep 改条件轮询）

替代固定 `time.sleep(n)` 等最终一致性 / 异步条件：
- 快机器不浪费（条件提前满足即返回）
- 慢机器不误报（超时前持续重试）
- 超时失败抛 AssertionError（测试失败信息明确，不静默）

用法（测试文件内直接导入，conftest 已把 backend/tests 加入 sys.path）：

    from polling import polling_until

    polling_until(
        lambda: any(h.content_id == "rag-001" for h in search(...).hits),
        timeout=5, interval=0.2, message="Qdrant 索引未就绪",
    )
"""
from __future__ import annotations

import time


def polling_until(
    cond,
    timeout: float = 3.0,
    interval: float = 0.1,
    message: str = "",
) -> bool:
    """轮询直到 cond() 为真（或求值抛错），超时抛 AssertionError。

    - cond：零参可调用，返回真值表示条件满足
    - timeout：总超时秒数
    - interval：两次轮询间隔秒数
    - message：超时失败时的补充说明
    成功返回 True；超时抛 AssertionError（含最后异常信息便于定位）。
    """
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            if cond():
                return True
        except Exception as exc:  # noqa: BLE001 —— 轮询期条件求值异常视为未满足
            last_error = exc
        time.sleep(interval)
    detail = f"轮询超时（{timeout:.1f}s）条件未满足"
    if message:
        detail += f": {message}"
    if last_error is not None:
        detail += f"；最后异常: {type(last_error).__name__}: {last_error}"
    raise AssertionError(detail)
