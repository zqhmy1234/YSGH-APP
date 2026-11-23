"""B5 canonical memory ledger tables.

These tables are *infrastructure state*, not new business entities. The public
read models still point at the four B5 layers (``user_profile`` for facts,
``contents`` for assets, ``correction_log``/``user_ai_persona`` for preferences,
and ``memory_entries`` for narrative memory).

Naming decision (B5-01): use the canonical ``memory_*`` names and keep the V3
``agent_v3_memory_*`` tables untouched for one release as a rollback snapshot.
We deliberately do not rename them in place: the V3 rows carry a string
``person_id`` while the canonical rows require a real ``users.id`` FK. A blind
rename would either fail the FK or silently bless unverified identities.

Every write must carry at least one evidence item. The DB check is the last line
of defence for the "no evidence, no memory" rule; the domain layer rejects first
so the user gets a receipt instead of a database exception.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy import (
    text as sa_text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class MemoryEntry(Base):
    """Narrative memory (B5 layer ①); one active row per episode/impression."""

    __tablename__ = "memory_entries"
    __table_args__ = (
        CheckConstraint("layer IN ('narrative', 'fact_claim')", name="ck_memory_entries_layer"),
        CheckConstraint(
            "kind IN ('episode', 'impression', 'story_segment', 'emotion_trace')",
            name="ck_memory_entries_kind",
        ),
        CheckConstraint(
            "sensitivity IN ('normal', 'sensitive', 'forbidden')",
            name="ck_memory_entries_sensitivity",
        ),
        CheckConstraint("jsonb_array_length(evidence) > 0", name="ck_memory_entries_evidence"),
        Index("ix_memory_entries_user_layer_active", "user_id", "layer", "valid_to"),
        Index("ix_memory_entries_user_arc", "user_id", "story_arc", "valid_from"),
        Index("ix_memory_entries_thread", "thread_id"),
    )

    entry_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    layer: Mapped[str] = mapped_column(String(16), default="narrative")
    kind: Mapped[str] = mapped_column(String(24))
    text: Mapped[str] = mapped_column(Text)
    summary: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    story_arc: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    superseded_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default=sa_text("'[]'::jsonb"))
    confidence: Mapped[float] = mapped_column(Numeric(3, 2), default=0.6, server_default=sa_text("0.60"))
    sensitivity: Mapped[str] = mapped_column(String(16), default="normal", server_default=sa_text("'normal'"))
    thread_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    card_hash: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(24), default="sculptor", server_default=sa_text("'sculptor'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryFactClaim(Base):
    """A candidate fact, never a second source of truth.

    ``proposed`` rows never enter context. Once the user confirms, the value is
    written through the existing profile path and this row becomes ``promoted``.
    """

    __tablename__ = "memory_fact_claims"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'promoted')",
            name="ck_memory_fact_claims_status",
        ),
        CheckConstraint("jsonb_array_length(evidence) > 0", name="ck_memory_fact_claims_evidence"),
        Index("ix_memory_fact_claims_user_status", "user_id", "status", "valid_to"),
    )

    claim_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    dimension: Mapped[str] = mapped_column(String(64))
    value: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default=sa_text("'[]'::jsonb"))
    status: Mapped[str] = mapped_column(String(16), default="proposed", server_default=sa_text("'proposed'"))
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    promoted_entry: Mapped[str | None] = mapped_column(Text, nullable=True)


class MemoryCorrection(Base):
    """Audit trail for explicit user corrections to memory objects."""

    __tablename__ = "memory_corrections"
    __table_args__ = (
        CheckConstraint(
            "target_type IN ('entry', 'fact_claim', 'emotion', 'role_card')",
            name="ck_memory_corrections_target_type",
        ),
        Index("ix_memory_corrections_user_target", "user_id", "target_type", "target_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    target_type: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[str] = mapped_column(String(64))
    field: Mapped[str] = mapped_column(String(32))
    value: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryBatch(Base):
    """Idempotency receipt for one nightly batch commit."""

    __tablename__ = "memory_batches"

    batch_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    receipt: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=sa_text("'{}'::jsonb"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryChangeLog(Base):
    """Before/after audit for every applied memory change."""

    __tablename__ = "memory_change_log"
    __table_args__ = (
        CheckConstraint("op IN ('insert', 'supersede', 'promote', 'delete')", name="ck_memory_change_log_op"),
        Index("ix_memory_change_log_user_at", "user_id", "at"),
        Index("ix_memory_change_log_batch", "batch_hash"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    batch_hash: Mapped[str] = mapped_column(String(64))
    entry_id: Mapped[str] = mapped_column(String(64))
    op: Mapped[str] = mapped_column(String(16))
    before: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    after: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default=sa_text("'[]'::jsonb"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MemoryDraft(Base):
    """Muse proposal waiting for the nightly Sculptor batch."""

    __tablename__ = "memory_drafts"
    __table_args__ = (
        CheckConstraint("jsonb_array_length(evidence) > 0", name="ck_memory_drafts_evidence"),
        Index("ix_memory_drafts_user_at", "user_id", "at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    layer: Mapped[str] = mapped_column(String(16), default="narrative")
    kind: Mapped[str] = mapped_column(String(24), default="episode")
    text: Mapped[str] = mapped_column(Text)
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default=sa_text("'[]'::jsonb"))
    reason: Mapped[str] = mapped_column(Text, default="", server_default=sa_text("''"))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = [
    "MemoryBatch",
    "MemoryChangeLog",
    "MemoryCorrection",
    "MemoryDraft",
    "MemoryEntry",
    "MemoryFactClaim",
]
