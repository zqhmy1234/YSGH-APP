"""事件契约（B3 四层事件模型：L0 瞬间 / L1 日 / L2 主题 / L3 流）"""
from datetime import datetime

from pydantic import BaseModel, Field


class EventOut(BaseModel):
    id: str
    level: int = Field(..., ge=0, le=3)
    title: str | None
    title_source: str | None            # llm / template / user
    cover_content_id: str | None
    start_time: datetime | None
    end_time: datetime | None
    place: str | None
    tags: list[str] = []
    emotion: dict | None
    sensitivity: str | None
    confidence: float | None
    status: str                         # draft / confirmed / rejected
    generated_by: str                   # device / cloud
    content_count: int = 0
    photo_count: int = 0


class EventMergeRequest(BaseModel):
    """用户手动合并事件（B3-5：存合并规则，用户操作优先）"""

    target_event_id: str
    source_event_ids: list[str] = Field(..., min_length=1)


class EventSplitRequest(BaseModel):
    """用户手动拆分事件"""

    event_id: str
    content_ids: list[str] = Field(..., min_length=1)


class EventConfirmRequest(BaseModel):
    """用户确认事件（置信度<0.7 转正；用户背书后算法不再改动）"""

    event_id: str
    title: str | None = None
