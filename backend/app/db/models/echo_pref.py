"""B5/B7: echo_preference and echo_exclusion (user-owned controls)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class EchoPreference(Base):
    __tablename__ = "echo_preference"

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    quiet_start: Mapped[str] = mapped_column(String(5), default="22:00", server_default=text("'22:00'"))
    quiet_end: Mapped[str] = mapped_column(String(5), default="08:00", server_default=text("'08:00'"))
    max_per_day: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    channels: Mapped[str] = mapped_column(String(64), default="in_app", server_default=text("'in_app'"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class EchoExclusion(Base):
    __tablename__ = "echo_exclusion"
    __table_args__ = (
        Index("ix_echo_exclusion_user_kind", "user_id", "kind"),
        UniqueConstraint("user_id", "kind", "person_id", "word", "date_from", "date_to", name="uq_echo_exclusion_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(16))  # person / word / date_range
    person_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    word: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = ["EchoExclusion", "EchoPreference"]
