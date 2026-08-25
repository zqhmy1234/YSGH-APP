"""emotion.py —— B5a 语音域钩子：情绪落库后消费

任务归属：Wave 4 Agent J（B5a 客户端/消费域）独占本文件。
当前为 no-op 占位；Agent J 在此实现：
- 语音情绪（PR#1 已实现：emotion/confidence/source/actionable）联动事件层
- events.emotion 写入（模型字段已存在，现零写入）
- 情绪关怀触发所需的情绪事件投递（触发逻辑在 notify.py，本钩子只负责数据联动）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models import Content


def consume_emotion(db: Session, content: Content) -> None:
    # TODO(Wave4-AgentJ): events.emotion 联动
    return None
