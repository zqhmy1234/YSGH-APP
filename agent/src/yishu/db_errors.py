"""数据层错误类型（替代 `postgrest.exceptions.APIError`）。

为什么需要它：上游工具/服务大量写成
    `from yishu.db_errors import APIError` + `except APIError as e: ... e.message`
若本地薄层抛的是普通 `ValueError`/`RuntimeError`，这些 `except` 分支**不再命中**，
异常会穿透到上层——行为静默改变（比报错更糟）。

故本类**继承 ValueError**（两侧兼容）：
  · `except APIError`（上游调用点）仍生效；
  · `except ValueError`（本仓 AG3 测试与通用兜底）同样生效；
  · 带 `.message`，与 PostgREST 的错误形状一致（上游读 `e.message`）。
"""
from __future__ import annotations

from typing import Any


class APIError(ValueError):
    """数据层错误（形状对齐 PostgREST：`message` + 可选 `code`/`details`）。"""

    def __init__(self, message: str, code: str | None = None, details: Any = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.details = details

    def __str__(self) -> str:  # 与 PostgREST 打印一致
        return self.message


__all__ = ["APIError"]
