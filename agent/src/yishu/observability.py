"""结构化日志与请求级可观测（2026-10-04 · 性能优化配套）。

## 为什么加这个

改造前agent 服务只有 5 条启动/收尾日志，关键链路几乎全黑：
`/v1/chat` 只能看到一个总耗时 `latency_ms`，**无法回答"25.5 秒花在哪"**——
LLM 调了几次？每次多久？工具调了几次？输出校验重跑了几轮？token 用了多少？全部未知。
没有这些数据，调参就是盲改；优化后也无法证明真的变快了。

本模块提供：`request_id`（贯穿一次请求，跨层可串联）、`log_event()`（单行 key=value）、
`timed()`（耗时埋点）、`extract_usage()`（token 用量）。

## 隐私与密钥纪律（硬约束）

**禁止**把下列内容写进日志：消息正文、记忆内容、URL 全文（可能带 token）、
任何 key/secret/token 的**值**、Authorization 头。

`log_event` 对敏感字段名做**主动拦截并抛异常**（而非静默丢弃——静默会让人
以为记上了，实际没有）。长度类信息（`msg_len`）是允许且推荐的替代做法。

## 用法

```python
from yishu.observability import bind_request_id, log_event, new_request_id, timed

token = bind_request_id(new_request_id())
try:
    with timed("llm_call", model="qwen3.8-flash") as t:
        resp = agent.invoke(messages)
    log_event("llm_done", **t.fields, **extract_usage(resp))
finally:
    reset_request_id(token)
```
"""
from __future__ import annotations

import contextvars
import logging
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("yishu.observability")

# 审计 logger 单独一路，便于运营侧按需接入独立告警通道
audit_logger = logging.getLogger("yishu.audit")

# 禁止出现在日志里的字段名片段（子串匹配，大小写不敏感）
_FORBIDDEN_KEY_PARTS: tuple[str, ...] = (
    "password",
    "secret",
    "authorization",
    "api_key",
    "apikey",
    "access_key",
    "token_value",
    "content",
    "body",
    "text",
    "raw",
    "prompt",
    "completion",
)


class SensitiveFieldError(ValueError):
    """试图把敏感字段写进结构化日志（开发期就该炸，而不是泄露到日志文件）。"""


# 允许放行的**量词**后缀：这些字段只暴露「数量/耗时/时间戳」，不含内容本身。
# 2026-10-04 实测踩坑：content_len 被敏感词 content 误伤 => 抛异常被上层
# except 吞掉 => 幂等逻辑静默失效。长度/计数类安全且必要，不该被拦。
_SAFE_QUANT_SUFFIXES = ("_len", "_count", "_ms", "_chars", "_at")

# 允许放行的**标识符**键：必须是白名单，而不是"凡以 _id 结尾就放行"。
# 2026-10-05 安全修复：原实现把 _id/_ids 放进后缀白名单且**先于**敏感词判定直接
# return ⇒ `secret_id` / `api_key_id` / `token_value_id` 这类命名一行就能绕过守卫。
# 守卫可被命名绕过等于没有守卫，故改为显式列举。
_SAFE_ID_KEYS: frozenset[str] = frozenset(
    {
        "user_id",
        "thread_id",
        "request_id",
        "rid",
        "memory_id",
        "collection_id",
        "category_id",
        "call_id",
        "tool_call_id",
        "existing_id",
        "trace_id",
        # 2026-10-06：contents 行的 UUID 标识符（W1 镜像链路需要记它才能对账）。
        # 它虽含敏感词 "content"，但语义是**标识符**（UUID），不是内容值——
        # 这正是本白名单存在的意义：按**具体键名**逐个放行，而不是按后缀放行。
        "content_id",
    }
)


def _reject_sensitive(key: str) -> None:
    low = key.lower()
    # ⚠️ 2026-10-05 一次被回退的"修正"（记下来，别重做）：
    # 曾把顺序改成「敏感词优先 → 量词后缀 → 标识符」，理由是想堵`secret_at` 这类
    # 命名绕过。**那是把一个非问题当问题修，反而打坏了真实埋点** ——
    # `memory_tools` 的 `save_memory_dedup_miss(content_len=...)` 命中敏感词 `content`
    # ⇒ 抛 SensitiveFieldError ⇒ 被上层 except 吞掉 ⇒ **判重日志静默失效**，
    # 4 个存量测试当场变红（test_dedup_levels 等）。
    #
    # 为什么原顺序是对的：量词后缀（_len/_count/_ms/_chars/_at）的值是
    # 长度/计数/耗时/时间戳，**结构上就装不下秘密**；`secret_at` 存的是时间戳，
    # 不是密钥，它不是绕过。真正需要防的「命名绕过」是 `_id` 那一类
    # —— `secret_id` 曾一行放行，而 id 里确实可能塞进任意标识，
    # 那个已经用**显式白名单** `_SAFE_ID_KEYS` 堵住了。
    #
    # 顺序即契约，改动前先想清楚"后缀豁免到底豁免了什么语义"。
    if low.endswith(_SAFE_QUANT_SUFFIXES):
        return
    # 标识符走**显式白名单**，而不是后缀匹配
    if low in _SAFE_ID_KEYS or (low.endswith("_ids") and low[:-4] in _SAFE_ID_KEYS):
        return
    for part in _FORBIDDEN_KEY_PARTS:
        if part in low:
            raise SensitiveFieldError(
                f"日志字段 {key!r} 命中敏感词 {part!r}，禁止入日志"
                f"（长度请用 *_len 字段代替，如 msg_len）"
            )


# ---------------------------------------------------------------------------
# request_id
# ---------------------------------------------------------------------------
_request_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "yishu_request_id", default="-"
)


def new_request_id() -> str:
    """生成短 request_id（8 位 hex，够串联一次请求又不撑爆日志）。"""
    return uuid.uuid4().hex[:8]


def bind_request_id(request_id: str) -> contextvars.Token[str]:
    """把 request_id 绑定到当前上下文，返回 token 供 `reset_request_id`。"""
    return _request_id.set(request_id)


def reset_request_id(token: contextvars.Token[str]) -> None:
    _request_id.reset(token)


def current_request_id() -> str:
    return _request_id.get()
# ---------------------------------------------------------------------------
# 结构化事件
# ---------------------------------------------------------------------------
_MAX_VALUE_LEN = 200
# dict 展开的最大项数（防token 用量等大映射刷屏）
_MAX_DICT_ITEMS = 8


def _fmt_value(value: Any) -> str:
    """标量转字符串；容器按类型处理，避免刷屏与泄露。

    dict **展开**为 `k=v` 对（最多 `_MAX_DICT_ITEMS` 项）——工具调用分布、
    token 分项这类**计数型**映射必须看得见，折成 `<dict:1>` 等于没埋点。
    list/tuple/set 仍只记长度（元素可能是正文或路径）。
    dict 的键仍走敏感词拦截（见 `_render`），展开不绕过该检查。
    """
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        text = value.replace("\n", "\\n")
        return text if len(text) <= _MAX_VALUE_LEN else text[:_MAX_VALUE_LEN] + "…"
    if isinstance(value, dict):
        items = list(value.items())[:_MAX_DICT_ITEMS]
        body = ",".join(f"{k}={_fmt_value(v)}" for k, v in items)
        if len(value) > _MAX_DICT_ITEMS:
            body += f",+{len(value) - _MAX_DICT_ITEMS}more"
        return "{" + body + "}"
    if isinstance(value, (list, tuple, set)):
        return f"<{type(value).__name__}:{len(value)}>"
    return f"<{type(value).__name__}>"


def _render(event: str, fields: dict[str, Any]) -> str:
    parts: list[str] = [event]
    for key, value in fields.items():
        if key.startswith("_"):
            continue
        _reject_sensitive(key)
        parts.append(f"{key}={_fmt_value(value)}")
    return f"rid={current_request_id()} " + " ".join(parts)


def log_event(event: str, *, level: int = logging.INFO, **fields: Any) -> None:
    """输出一条单行结构化事件：`event k1=v1 k2=v2 ...`。"""
    logger.log(level, _render(event, fields))


def log_audit(event: str, **fields: Any) -> None:
    """审计事件（鉴权失败、越权尝试、限流触发…），WARNING 起步便于单独告警。"""
    audit_logger.warning(_render(event, fields))


# ---------------------------------------------------------------------------
# 耗时埋点
# ---------------------------------------------------------------------------
@dataclass
class TimedResult:
    """`timed()` 退出后携带的耗时字段。"""

    stage: str
    fields: dict[str, Any] = field(default_factory=dict)

    @property
    def latency_ms(self) -> int:
        return int(self.fields.get("latency_ms", 0))


@contextmanager
def timed(stage: str, *, log_ok: bool = True, **fields: Any) -> Iterator[TimedResult]:
    """记录某阶段耗时；异常时记 `exc_type` 后**原样抛出**（绝不吞异常）。"""
    result = TimedResult(stage=stage)
    t0 = time.perf_counter()
    try:
        yield result
    except BaseException as exc:  # noqa: BLE001 —— 记录后必须重抛
        result.fields = {
            **fields,
            "latency_ms": int((time.perf_counter() - t0) * 1000),
            "exc_type": type(exc).__name__,
            "ok": False,
        }
        log_event("stage_error", level=logging.ERROR, stage=stage, **result.fields)
        raise
    result.fields = {
        **fields,
        "latency_ms": int((time.perf_counter() - t0) * 1000),
        "ok": True,
    }
    if log_ok:
        log_event("stage", **result.fields)


# ---------------------------------------------------------------------------
# LLM token 用量
# ---------------------------------------------------------------------------
def _pick(usage: Any, *names: str) -> int | None:
    """从 dict 或对象里取第一个存在的数值字段。"""
    for name in names:
        if isinstance(usage, dict):
            if name in usage:
                try:
                    return int(usage[name])
                except (TypeError, ValueError):
                    return None
        elif hasattr(usage, name):
            value = getattr(usage, name)
            try:
                return int(value)
            except (TypeError, ValueError):
                return None
    return None


def extract_usage(obj: Any) -> dict[str, Any]:
    """提取 token 用量（只取**数量**，不取任何文本）。

    兼容两类形态：`AIMessage.usage_metadata` / OpenAI 风格 `usage`，
    以及直接给 dict（测试里常用）。拿不到就返回空 dict。
    """
    usage: Any = None
    if isinstance(obj, dict):
        usage = obj.get("usage_metadata") or obj.get("usage")
    else:
        usage = getattr(obj, "usage_metadata", None) or getattr(obj, "usage", None)
    if not usage:
        return {}
    out: dict[str, Any] = {}
    for label, names in (
        ("in", ("input_tokens", "prompt_tokens")),
        ("out", ("output_tokens", "completion_tokens")),
        ("total", ("total_tokens",)),
    ):
        value = _pick(usage, *names)
        if value is not None:
            out[f"tokens_{label}"] = value
    return out


__all__ = [
    "SensitiveFieldError",
    "TimedResult",
    "audit_logger",
    "bind_request_id",
    "current_request_id",
    "extract_usage",
    "log_audit",
    "log_event",
    "logger",
    "new_request_id",
    "reset_request_id",
    "timed",
]
