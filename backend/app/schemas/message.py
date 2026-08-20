"""消息中心契约（S4-08）"""
from datetime import datetime

from pydantic import BaseModel, Field


class MessageOut(BaseModel):
    """消息出参（in-app 列表项 / push 记录）"""

    id: int
    channel: str
    msg_type: str
    title: str
    body: str
    payload: dict = Field(default_factory=dict)
    status: str
    sent_at: datetime
    read_at: datetime | None = None
