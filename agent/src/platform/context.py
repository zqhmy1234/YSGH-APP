"""请求上下文（替代 `coze_coding_utils.runtime_ctx.context`）。

上游用法（全仓一致）：
  · `request_context.get()` 取当前请求上下文；无则 `new_context(method=...)` 造一个；
  · 工具内 `_get_user_id()` 从上下文取 user_id（`tools/memory_tools.py:28-33`、
    `tools/url_fetch_tools.py:25-30`），兜底 `os.getenv("COZE_USER_ID", "default")`。

为什么用 contextvar：与上游同为**协程/线程安全的隐式传递**；但本服务**不把 user_id 当可信输入**——
它由我们后端反代时注入（JWT → 可信 user_id），见 AG5。
"""
from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
from typing import Any


@dataclass
class Context:
    """请求上下文（字段名与上游兼容：method / user_id / run_id / 附加键）。"""

    method: str = "unknown"
    user_id: str | None = None
    run_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        return self.extra.get(key, default)


request_context: contextvars.ContextVar[Context | None] = contextvars.ContextVar(
    "yishu_request_context", default=None
)


def new_context(**kwargs: Any) -> Context:
    """造一个上下文（上游签名容忍任意关键字；未知键进 `extra`）。"""
    known = {"method", "user_id", "run_id"}
    extra = {k: v for k, v in kwargs.items() if k not in known}
    return Context(
        method=kwargs.get("method", "unknown"),
        user_id=kwargs.get("user_id"),
        run_id=kwargs.get("run_id"),
        extra=extra,
    )


def set_context(ctx: Context | None) -> contextvars.Token:
    """设定当前上下文（返回 token 供 reset；上游 agent.py 用 `request_context.set(ctx)`）。"""
    return request_context.set(ctx)


def current_user_id(default: str = "default") -> str:
    """取当前 user_id；**不读环境变量冒充用户**（上游兜底 `COZE_USER_ID` 已移除）。

    上游兜底会把"没注入 user_id"静默变成一个固定用户，等于把多用户数据混在一起——
    故本实现只认显式注入，缺失即返回 default 并由调用方决定如何处理（AG5 会在反代层保证注入）。
    """
    ctx = request_context.get()
    if ctx is not None and ctx.user_id:
        return ctx.user_id
    return default


# 上游 `from coze_coding_utils.runtime_ctx.context import request_context` 的兼容别名
__all__ = ["Context", "current_user_id", "new_context", "request_context", "set_context"]
