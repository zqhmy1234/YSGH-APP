"""B5 canonical ledger: memory_entries / fact_claims / corrections / batches /
change_log / drafts.

Revision ID: f6a7b8c9d0e1
Revises: d4e5f6a7b8c9

This is an additive migration. The V3 ``agent_v3_memory_*`` tables are kept
untouched for one release as a rollback snapshot; we do not rename them because
their ``person_id`` is a free-form string and cannot satisfy the canonical
``user_id -> users.id`` foreign key. Data migration is a separate, reviewable
step (see B5-01 runbook).

Downgrade drops only the six canonical tables. No business table is touched.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f6a7b8c9d0e1"
down_revision: str | Sequence[str] | None = "d4e5f6a7b8c9"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "memory_entries",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layer", sa.String(16), nullable=False, server_default=sa.text("'narrative'")),
        sa.Column("kind", sa.String(24), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("story_arc", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("superseded_by", sa.String(64), nullable=True),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("confidence", sa.Numeric(3, 2), nullable=False, server_default=sa.text("0.60")),
        sa.Column("sensitivity", sa.String(16), nullable=False, server_default=sa.text("'normal'")),
        sa.Column("thread_id", postgresql.UUID(as_uuid=False), nullable=True),
        sa.Column("card_hash", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(24), nullable=False, server_default=sa.text("'sculptor'")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("layer IN ('narrative', 'fact_claim')", name="ck_memory_entries_layer"),
        sa.CheckConstraint(
            "kind IN ('episode', 'impression', 'story_segment', 'emotion_trace')",
            name="ck_memory_entries_kind",
        ),
        sa.CheckConstraint(
            "sensitivity IN ('normal', 'sensitive', 'forbidden')",
            name="ck_memory_entries_sensitivity",
        ),
        sa.CheckConstraint("jsonb_array_length(evidence) > 0", name="ck_memory_entries_evidence"),
    )
    op.create_index(
        "ix_memory_entries_user_layer_active", "memory_entries", ["user_id", "layer", "valid_to"]
    )
    op.create_index(
        "ix_memory_entries_user_arc", "memory_entries", ["user_id", "story_arc", "valid_from"]
    )
    op.create_index("ix_memory_entries_thread", "memory_entries", ["thread_id"])

    op.create_table(
        "memory_fact_claims",
        sa.Column("claim_id", sa.String(64), primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dimension", sa.String(64), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("status", sa.String(16), nullable=False, server_default=sa.text("'proposed'")),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("recorded_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("promoted_entry", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'promoted')",
            name="ck_memory_fact_claims_status",
        ),
        sa.CheckConstraint("jsonb_array_length(evidence) > 0", name="ck_memory_fact_claims_evidence"),
    )
    op.create_index(
        "ix_memory_fact_claims_user_status", "memory_fact_claims", ["user_id", "status", "valid_to"]
    )

    op.create_table(
        "memory_corrections",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("target_type", sa.String(16), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("field", sa.String(32), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "target_type IN ('entry', 'fact_claim', 'emotion', 'role_card')",
            name="ck_memory_corrections_target_type",
        ),
    )
    op.create_index(
        "ix_memory_corrections_user_target", "memory_corrections", ["user_id", "target_type", "target_id"]
    )

    op.create_table(
        "memory_batches",
        sa.Column("batch_hash", sa.String(64), primary_key=True),
        sa.Column(
            "receipt",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )

    op.create_table(
        "memory_change_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("batch_hash", sa.String(64), nullable=False),
        sa.Column("entry_id", sa.String(64), nullable=False),
        sa.Column("op", sa.String(16), nullable=False),
        sa.Column("before", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("after", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint(
            "op IN ('insert', 'supersede', 'promote', 'delete')", name="ck_memory_change_log_op"
        ),
    )
    op.create_index("ix_memory_change_log_user_at", "memory_change_log", ["user_id", "at"])
    op.create_index("ix_memory_change_log_batch", "memory_change_log", ["batch_hash"])

    op.create_table(
        "memory_drafts",
        sa.Column("id", sa.BigInteger(), autoincrement=True, primary_key=True),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("layer", sa.String(16), nullable=False, server_default=sa.text("'narrative'")),
        sa.Column("kind", sa.String(24), nullable=False, server_default=sa.text("'episode'")),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column(
            "evidence",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("reason", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.CheckConstraint("jsonb_array_length(evidence) > 0", name="ck_memory_drafts_evidence"),
    )
    op.create_index("ix_memory_drafts_user_at", "memory_drafts", ["user_id", "at"])


def downgrade() -> None:
    op.drop_index("ix_memory_drafts_user_at", table_name="memory_drafts")
    op.drop_table("memory_drafts")
    op.drop_index("ix_memory_change_log_batch", table_name="memory_change_log")
    op.drop_index("ix_memory_change_log_user_at", table_name="memory_change_log")
    op.drop_table("memory_change_log")
    op.drop_table("memory_batches")
    op.drop_index("ix_memory_corrections_user_target", table_name="memory_corrections")
    op.drop_table("memory_corrections")
    op.drop_index("ix_memory_fact_claims_user_status", table_name="memory_fact_claims")
    op.drop_table("memory_fact_claims")
    op.drop_index("ix_memory_entries_thread", table_name="memory_entries")
    op.drop_index("ix_memory_entries_user_arc", table_name="memory_entries")
    op.drop_index("ix_memory_entries_user_layer_active", table_name="memory_entries")
    op.drop_table("memory_entries")
