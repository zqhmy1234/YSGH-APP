"""B7-01: user_ai_persona — three written relationship choices, one row per user."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, SmallInteger, String, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserAIPersona(Base):
    __tablename__ = "user_ai_persona"
    __table_args__ = (
        CheckConstraint("initiative BETWEEN 0 AND 2", name="ck_persona_initiative"),
        CheckConstraint("density BETWEEN 0 AND 2", name="ck_persona_density"),
        CheckConstraint("advice_bias BETWEEN 0 AND 2", name="ck_persona_advice_bias"),
    )

    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    # Three positions (0/1/2) behind three full-sentence relationship choices.
    # The numbers are storage only: API/prompt always return prose.
    initiative: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    density: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    advice_bias: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    role_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    version: Mapped[int] = mapped_column(default=0, server_default=text("0"))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    # Legacy columns kept for rollback and historical rows; not read by the new
    # relationship path and not exposed in the persona API.
    warmth: Mapped[int] = mapped_column(SmallInteger, default=3, server_default=text("3"))
    verbosity: Mapped[int] = mapped_column(SmallInteger, default=2, server_default=text("2"))
    advice: Mapped[int] = mapped_column(SmallInteger, default=1, server_default=text("1"))
    tone: Mapped[str] = mapped_column(String(16), default="gentle", server_default=text("'gentle'"))
    emotion_display: Mapped[str] = mapped_column(String(16), default="gentle", server_default=text("'gentle'"))


__all__ = ["UserAIPersona"]
