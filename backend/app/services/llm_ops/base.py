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
    """护栏检测（规则预检 + qwen-flash 双保险；生产未配 key → fail-safe 拒发）"""
    return dashscope.moderate(text)
