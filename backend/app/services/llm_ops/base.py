"""base.py —— llm_ops 底层转发（唯一允许触碰 dashscope 的模块）

Wave 0 建立。后续若需要新底层能力（如流式/新模型），由集成 Agent 评估后
在此增加转发函数，各域 Agent 不得直接 import dashscope。
"""
from __future__ import annotations

from typing import Any

from app.services.external import dashscope


def llm_available() -> bool:
    """LLM 是否可用（未配 key / mock 模式 → False）"""
    return dashscope._llm_available()  # noqa: SLF001 —— 同仓转发，接受私有访问


def chat_text(system: str, user: str, model: str = dashscope.QWEN_FLASH) -> str:
    """统一文本对话入口（改写/精排/归并/标注/护栏复用）"""
    return dashscope._chat_text(system, user, model)  # noqa: SLF001


def rewrite_query(q: str) -> str:
    return dashscope.rewrite_query(q)


def route_query(q: str) -> str:
    return dashscope.route_query(q)


def moderate(text: str) -> dict[str, Any]:
    """护栏检测（Wave2-F 2026-08-26 起：百炼托管优先、chat 兜底）

    策略（B5b-1 定稿）：托管护栏可用（qwen_response_check，X-DashScope-DataInspection
    header，见 guard_managed.py）→ 托管判定；托管不可用/异常 → dashscope.moderate
    （规则预检 + qwen-flash chat 双保险；生产未配 key → fail-safe 拒发）。
    mock 模式：托管不可用 → 走 dashscope.moderate 的 mock 契约（规则命中即拦截，
    否则放行），保持本地联调确定性。
    """
    from app.services.llm_ops.guard_managed import qwen_response_check

    try:
        return qwen_response_check(text)
    except RuntimeError as exc:
        logger = __import__("logging").getLogger("yishu.llm_ops")
        logger.info("托管护栏不可用，chat 兜底: %s", exc)
        return dashscope.moderate(text)
