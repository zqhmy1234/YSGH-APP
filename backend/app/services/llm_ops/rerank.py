"""rerank.py —— B2 域：第二层 LLM 精排（Ilya 方案）

任务归属：Wave 2 Agent F（M1 补遗域）独占本文件。
实现目标（B2-1）：bge-reranker 粗排 top-50→top-10 → 本模块 qwen-flash 精排 → top-5，
精排判断"这段能不能回答这个问题"，经 base.chat_text 调用，禁止直接 import dashscope。
"""
from __future__ import annotations


def llm_rerank(query: str, hits: list[dict]) -> list[dict]:
    """TODO(Wave2-AgentF): LLM 精排实现；当前原样返回。"""
    return hits
