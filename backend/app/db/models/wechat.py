"""微信域 ORM 模型（wechat_messages，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class WechatMessage(Base):
    """wechat_messages 表（F6 微信入口：msg_id 幂等，只收不编）"""

    __tablename__ = "wechat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    msg_type: Mapped[str] = mapped_column(String)  # text / image / link / voice
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="processed")  # processed / failed / deleted
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
