"""B7-04: crisis_events — level + record_status + escalation_note only.

There is deliberately no "contacted emergency contact" column: the product
cannot contact anyone, and a fake boolean would be the worst kind of lie.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, CheckConstraint, DateTime, String, Text, func, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class CrisisEvent(Base):
    __tablename__ = "crisis_events"
    __table_args__ = (
        CheckConstraint("level IN ('t1','t2','t3')", name="ck_crisis_level"),
        CheckConstraint(
            "record_status IN ('recorded','failed','not_applicable')",
            name="ck_crisis_record_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    level: Mapped[str] = mapped_column(String(8))  # t1 / t2 / t3
    trigger: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    action: Mapped[str] = mapped_column(String(32), default="short_circuit", server_default=text("'short_circuit'"))
    record_status: Mapped[str] = mapped_column(String(32), default="recorded", server_default=text("'recorded'"))
    resource_given: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    escalation_note: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = ["CrisisEvent"]
