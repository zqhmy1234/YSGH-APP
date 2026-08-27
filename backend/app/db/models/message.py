"""消息/推送域 ORM 模型（messages，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class Message(Base):
    """messages 表（S4-07 推送 + S4-08 消息中心）

    统一消息中心：in-app 消息（关怀追问/回响）与 push 记录（每日复盘/语音完成）
    同表存储；产品部推送策略：复盘 push / 回响 in-app / 关怀追问 in-app。
    推送厂商凭证未配置 → mock 通道（notify.py 日志占位），配 key 后零切换。
    """

    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    channel: Mapped[str] = mapped_column(String, default="in_app")  # in_app / push
    msg_type: Mapped[str] = mapped_column(String)  # daily_review / voice_done / care_followup / echo
    title: Mapped[str] = mapped_column(String)
    body: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSONB, default=dict)  # 附加数据（内容 id / 语音 id / 模板标记）
    status: Mapped[str] = mapped_column(String, default="unread")  # unread / read / archived
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
