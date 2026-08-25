"""sensitive.py —— B5b 护栏域钩子：事件级敏感标记

任务归属：Wave 1 Agent C（B5b 护栏域）独占本文件。
当前为 no-op 占位；Agent C 在此实现：
- 规则词表先行 + LLM 补漏的事件级敏感分类（5-8 类：分手/离世/健康/金钱/家庭矛盾…）
- 命中 → 写 contents.sensitive_tags（现零写入）+ sensitive_status
- 配合重建后的 sensitive_words 表（level 2/3 回流）与 profile_sensitive 双查接线
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models import Content


def mark_sensitive_on_ingest(db: Session, content: Content) -> None:
    # TODO(Wave1-AgentC): 事件级敏感分类器接入（规则先行 + LLM 补漏）
    return None
