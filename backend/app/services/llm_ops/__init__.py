"""llm_ops —— LLM 能力聚合层（Wave 0 建立，2026-08-26）

目的：多 Agent 并行时 dashscope.py（底层 client）冻结只读，
各功能域的 LLM 调用统一在本包扩展（每域一个模块文件），经 base.py 转发底层。

约定：
- base.py 只转发 dashscope 现有函数，禁止修改 dashscope.py
- 各域 Agent 在自己的模块（rerank.py / event_merge.py / annotate.py / guard.py）内实现，
  需要新 LLM 调用时经 _chat_text 等 base 函数组合，不直接 import dashscope 内部
"""
from app.services.llm_ops.base import (
    chat_text,
    llm_available,
    moderate,
    rewrite_query,
    route_query,
)

__all__ = [
    "chat_text",
    "llm_available",
    "moderate",
    "rewrite_query",
    "route_query",
]
