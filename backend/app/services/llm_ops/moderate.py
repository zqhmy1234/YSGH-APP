"""moderate.py —— 内容安全护栏策略选择器（托管优先、chat 兜底）

任务归属：重构批次 A1（重构侦察 P0-1 解环，2026-08-27）。

背景（audit_B5b #1 + 重构侦察 P0-1）：原策略逻辑同时内联在 llm_ops/base.py 与
llm_ops/guard_managed.py 中，两者互相兜底形成全图唯一模块环：
  base.py:40            from ...guard_managed import qwen_response_check（函数级）
  guard_managed.py:154  from ...base import moderate as _chat_moderate（函数级）
本模块把"托管优先、chat 兜底"策略选择器收敛到这里，依赖方向收敛为单向 DAG：
  base.moderate → 本选择器 → guard_managed（托管检测）+ dashscope（chat 兜底）
  guard_managed.moderate_managed 的 chat 兜底直接走 dashscope（不再回头依赖 base）。

策略（B5b-1 定稿）：
1. 托管护栏可用（qwen_response_check，X-DashScope-DataInspection header，
   见 guard_managed.py）→ 托管判定（pass/reject 由托管裁决）；
2. 托管不可用/异常 → dashscope.moderate（规则预检 + qwen-flash chat 双保险；
   生产未配 key → fail-safe 拒发）。
mock 模式：托管不可用 → 走 dashscope.moderate 的 mock 契约（规则命中即拦截，
否则放行），保持本地联调确定性。
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.external import dashscope
from app.services.llm_ops.guard_managed import qwen_response_check

logger = logging.getLogger("yishu.llm_ops")


def moderate(text: str) -> dict[str, Any]:
    """护栏检测（B5b-1 定稿）：托管优先、chat 兜底

    托管路径返回 {"pass", "reason", "detector", "detail"}；chat 兜底返回
    dashscope.moderate 契约 {"pass", "reason", ...}。签名与返回值与
    llm_ops.base.moderate 完全一致，调用方零感知。
    """
    try:
        return qwen_response_check(text)
    except RuntimeError as exc:
        logger.info("托管护栏不可用，chat 兜底: %s", exc)
        return dashscope.moderate(text)
