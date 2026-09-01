"""事件域 ORM 模型（events/event_items/event_edit_log，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, String, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models._base import _uuid
from app.db.session import Base


class Event(Base):
    """events 表（B3 四层事件模型 L0-L3；2026-08-20 管线接线新增 ORM）"""

    __tablename__ = "events"
    __table_args__ = (
        # S-SY-1（B3-6 端侧 L0/L1 真值）：客户端事件幂等键（同用户唯一）。
        # PG 多 NULL 不冲突 → 部分唯一索引：仅非空 client_event_id 参与唯一约束。
        Index(
            "uq_events_user_client_event",
            "user_id",
            "client_event_id",
            unique=True,
            postgresql_where=sa_text("client_event_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    client_event_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=False)
    level: Mapped[int] = mapped_column(default=0)  # 0-3
    parent_event_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    title_source: Mapped[str | None] = mapped_column(String, nullable=True)
    cover_content_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    start_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    end_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    place: Mapped[str | None] = mapped_column(String, nullable=True)
    emotion: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sensitivity: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String, default="draft")
    generated_by: Mapped[str] = mapped_column(String, default="cloud")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class EventItem(Base):
    """event_items 表（content_id, event_id 多对多；层级 JOIN events.level）"""

    __tablename__ = "event_items"

    content_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class EventEditLog(Base):
    """event_edit_log 表（B3-5 用户合并/拆分/确认/重命名痕迹；AGG-013 用户操作优先）"""

    __tablename__ = "event_edit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    action: Mapped[str] = mapped_column(String)  # merge/split/confirm/rename
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
