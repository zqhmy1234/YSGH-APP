"""guard.py —— B5b 域：护栏扩展（违规词回流 / 事件级敏感 LLM 补漏）

任务归属：Wave 1 Agent C（B5b 护栏域）独占本文件。
实现目标：
- 事件级敏感分类 LLM 补漏（规则未命中 → 本模块判敏感，抓"他说以后别联系了"类表达）
- 违规词回流（百炼/检测违规 → 写 sensitive_words level=3）
现有规则预检 + moderate 已由 base.moderate 提供，本模块只做扩展。
"""
from __future__ import annotations


def detect_event_sensitive(text: str) -> list[str]:
    """TODO(Wave1-AgentC): 事件级敏感 LLM 补漏；当前返回空。"""
    return []
