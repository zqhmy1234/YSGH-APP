"""时间胶囊域 ORM 模型（capsules，BA2 批 · 远期总账 A5）

产品语义（2026-09-02 峰宝拍板：全量含到期推送）：
用户选一条记忆（contents 行）+ 一个未来时间封存；到期后可开启回看，
到期时经站内消息提醒（msg_type=capsule_due，走 notify.create_message）。

status 流转（幂等口径，扫描依赖它保证同胶囊只产一次到期消息）：
  sealed ——(capsule_scan 扫到 open_at<=now)——> due ——(用户 open)——> opened
  - sealed：封存中（未到期）
  - due：已到期未开启（到期消息已产出；扫描置位即消息幂等键）
  - opened：已开启回看（opened_at 落时间）
"""
from datetime import datetime

from sqlalchemy import DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models._base import _uuid
from app.db.session import Base


class Capsule(Base):
    """capsules 表（BA2 时间胶囊）"""

    __tablename__ = "capsules"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    # 被封存记忆（contents.id；不加 FK——与 messages.content_id 同口径，软删生态零侵入）
    content_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)  # 给未来自己的话
    sealed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    open_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))  # 到期时间
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # sealed / due / opened（流转语义见模块 docstring；index 供扫描 WHERE 走索引）
    status: Mapped[str] = mapped_column(String, default="sealed", index=True)
