"""微信域 ORM 模型（wechat_messages，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base

# wechat_messages.status **取值唯一来源**（D09-10 / B11 声明漂移）
#
# 原状三处不一致：本模块注释只列 3 个值、`services/wechat/service.py` 实际写 5 个值、
# `backend/sql/schema.sql` 注释只列 2 个（且其 DDL 默认值 `'processing'` 与本 ORM 默认 `"processed"`
# **不同**——默认值收敛需改 DDL ⇒ 属漂移迁移，已按台账 §5.10 拍板 10.3③ 登记待专用窗口，
# 本次**只对齐"取值集合"的声明**，不改任何默认值/行为）。
# 服务端实际写入点见 `services/wechat/service.py`（processing/processed/media_failed/sensitive/deleted）。
WECHAT_MESSAGE_STATUSES: tuple[str, ...] = (
    "processing",    # DDL 默认值（schema.sql）；当前代码路径均显式写入，故通常不落此值
    "processed",     # 处理完成（正常）
    "media_failed",  # 企微媒体下载失败
    "sensitive",     # 内容安全命中（不进云端镜像）
    "deleted",       # 用户软删本条（F6：只删本条）
)


class WechatMessage(Base):
    """wechat_messages 表（F6 微信入口：msg_id 幂等，只收不编）"""

    __tablename__ = "wechat_messages"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    msg_id: Mapped[str] = mapped_column(String, unique=True)
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    msg_type: Mapped[str] = mapped_column(String)  # text / image / link / voice
    content: Mapped[str | None] = mapped_column(Text, nullable=True)
    media_id: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="processed")  # 取值见 WECHAT_MESSAGE_STATUSES（上）
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
