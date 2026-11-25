"""B5/B7 foundation: persons, content_persons, user_ai_persona, crisis_events,
echo_preference, echo_exclusion, and correction_log.field.

Revision ID: d4e5f6a7b8c9
Revises: b8c4d2f0a1e5, c1d2e3f4a5b6

This migration intentionally merges the two pre-existing heads so a fresh
``alembic upgrade head`` has a single tip. It creates only new tables plus one
non-breaking defaulted column; no existing table is rewritten.

Rollback is symmetric: dropping these tables restores the pre-B5 shape (the
correction_log.field column is dropped last). Existing business data in
``contents`` / ``users`` / ``correction_log`` is never touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "d4e5f6a7b8c9"
down_revision: str | Sequence[str] | None = ("b8c4d2f0a1e5", "c1d2e3f4a5b6")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("id", postgresql.UUID(as_uuid=False), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("display_name", sa.String(80), nullable=False),
        sa.Column("name_norm", sa.String(80), nullable=False),
        sa.Column("relation", sa.String(32), server_default=sa.text("'other'"), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("notes", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("source", sa.String(32), server_default=sa.text("'user'"), nullable=False),
        sa.Column("confidence", sa.Float(), server_default=sa.text("1.0"), nullable=False),
        sa.Column("epistemic", sa.String(32), server_default=sa.text("'user_confirmed'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("epistemic IN ('user_confirmed', 'imported')", name="ck_persons_epistemic"),
    )
    op.create_index("ix_persons_user_deleted", "persons", ["user_id", "deleted_at"])
    op.create_index("ix_persons_user_name_norm", "persons", ["user_id", "name_norm"])

    op.create_table(
        "content_persons",
        sa.Column("content_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("contents.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("person_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("persons.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("valid_from", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("epistemic", sa.String(32), server_default=sa.text("'user_confirmed'"), nullable=False),
        sa.Column("source", sa.String(32), server_default=sa.text("'user'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_content_persons_person_active", "content_persons", ["person_id", "valid_to"])
    op.create_index("ix_content_persons_content_active", "content_persons", ["content_id", "valid_to"])

    op.create_table(
        "user_ai_persona",
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("warmth", sa.SmallInteger(), server_default=sa.text("3"), nullable=False),
        sa.Column("verbosity", sa.SmallInteger(), server_default=sa.text("2"), nullable=False),
        sa.Column("advice", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("tone", sa.String(16), server_default=sa.text("'gentle'"), nullable=False),
        sa.Column("emotion_display", sa.String(16), server_default=sa.text("'gentle'"), nullable=False),
        sa.Column("role_id", sa.String(64), nullable=True),
        sa.Column("version", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("warmth BETWEEN 1 AND 5", name="ck_persona_warmth"),
        sa.CheckConstraint("verbosity BETWEEN 1 AND 5", name="ck_persona_verbosity"),
        sa.CheckConstraint("advice BETWEEN 0 AND 2", name="ck_persona_advice"),
        sa.CheckConstraint("tone IN ('gentle','calm','playful','quiet','direct')", name="ck_persona_tone"),
        sa.CheckConstraint("emotion_display IN ('off','gentle','explicit')", name="ck_persona_emotion_display"),
    )

    op.create_table(
        "crisis_events",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("level", sa.String(8), nullable=False),
        sa.Column("trigger", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("action", sa.String(32), server_default=sa.text("'short_circuit'"), nullable=False),
        sa.Column("record_status", sa.String(32), server_default=sa.text("'recorded'"), nullable=False),
        sa.Column("resource_given", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("escalation_note", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        # No "contacted" column by design (B7-04): the product cannot contact anyone.
        sa.CheckConstraint("level IN ('t1','t2','t3')", name="ck_crisis_level"),
        sa.CheckConstraint(
            "record_status IN ('recorded','failed','not_applicable')", name="ck_crisis_record_status"
        ),
    )
    op.create_index("ix_crisis_events_user_id", "crisis_events", ["user_id"])

    op.create_table(
        "echo_preference",
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("quiet_start", sa.String(5), server_default=sa.text("'22:00'"), nullable=False),
        sa.Column("quiet_end", sa.String(5), server_default=sa.text("'08:00'"), nullable=False),
        sa.Column("max_per_day", sa.SmallInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("channels", sa.String(64), server_default=sa.text("'in_app'"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "echo_exclusion",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("person_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("word", sa.Text(), server_default=sa.text("''"), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=True),
        sa.Column("date_to", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint(
            "user_id", "kind", "person_id", "word", "date_from", "date_to", name="uq_echo_exclusion_key"
        ),
        sa.CheckConstraint("kind IN ('person','word','date_range')", name="ck_echo_exclusion_kind"),
    )
    op.create_index("ix_echo_exclusion_user_kind", "echo_exclusion", ["user_id", "kind"])

    op.add_column(
        "correction_log",
        sa.Column("field", sa.String(32), server_default=sa.text("'text'"), nullable=False),
    )
    op.execute("UPDATE correction_log SET field = 'text' WHERE field IS NULL")


def downgrade() -> None:
    op.drop_column("correction_log", "field")
    op.drop_index("ix_echo_exclusion_user_kind", table_name="echo_exclusion")
    op.drop_table("echo_exclusion")
    op.drop_table("echo_preference")
    op.drop_index("ix_crisis_events_user_id", table_name="crisis_events")
    op.drop_table("crisis_events")
    op.drop_table("user_ai_persona")
    op.drop_index("ix_content_persons_content_active", table_name="content_persons")
    op.drop_index("ix_content_persons_person_active", table_name="content_persons")
    op.drop_table("content_persons")
    op.drop_index("ix_persons_user_name_norm", table_name="persons")
    op.drop_index("ix_persons_user_deleted", table_name="persons")
    op.drop_table("persons")
