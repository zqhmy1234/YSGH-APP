"""B5-03: user-confirmed persons + content_persons relation.

Rules:
  * only ``user_confirmed`` or ``imported`` epistemology; no AI-inferred rows;
  * content_persons keeps validity windows; on databases without btree_gist we
    fall back to two columns + a partial unique index (the migration owns that
    branch, the ORM declares the same columns either way);
  * nothing here is read by the agent directly — only via L3 endpoints.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Index, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models._base import _uuid
from app.db.session import Base


class Person(Base):
    __tablename__ = "persons"
    __table_args__ = (
        Index("ix_persons_user_deleted", "user_id", "deleted_at"),
        Index("ix_persons_user_name_norm", "user_id", "name_norm"),
    )

    id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), primary_key=True, default=_uuid, server_default=text("gen_random_uuid()")
    )
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"))
    display_name: Mapped[str] = mapped_column(String(80))
    name_norm: Mapped[str] = mapped_column(String(80))
    relation: Mapped[str] = mapped_column(String(32), default="other", server_default=text("'other'"))
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    source: Mapped[str] = mapped_column(String(32), default="user", server_default=text("'user'"))
    confidence: Mapped[float] = mapped_column(Float, default=1.0, server_default=text("1.0"))
    epistemic: Mapped[str] = mapped_column(String(32), default="user_confirmed")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ContentPerson(Base):
    __tablename__ = "content_persons"
    __table_args__ = (
        Index("ix_content_persons_person_active", "person_id", "valid_to"),
        Index("ix_content_persons_content_active", "content_id", "valid_to"),
    )

    content_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True
    )
    person_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    epistemic: Mapped[str] = mapped_column(String(32), default="user_confirmed")
    source: Mapped[str] = mapped_column(String(32), default="user", server_default=text("'user'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = ["ContentPerson", "Person"]
