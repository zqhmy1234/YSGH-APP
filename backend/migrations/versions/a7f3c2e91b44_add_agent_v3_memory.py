"""agent-v3 memory ledger tables (new tables only, no in-place column edits).

Revision ID: a7f3c2e91b44
Revises: e1b1c1d1e1f1
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7f3c2e91b44"
down_revision: Union[str, Sequence[str], None] = "e1b1c1d1e1f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_v3_memory_entries",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("recorded_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("superseded_by", sa.String(64), nullable=True),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("correction", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.create_table(
        "agent_v3_memory_state",
        sa.Column("id", sa.SmallInteger(), primary_key=True),
        sa.Column("doc", postgresql.JSONB(), nullable=False),
    )

    op.create_index("ix_v3_memory_active", "agent_v3_memory_entries", ["person_id", "layer", "valid_to"])

    op.create_table(
        "agent_v3_memory_corrections",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("entry_id", sa.String(64), nullable=True),
        sa.Column("field", sa.String(32), nullable=False),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "agent_v3_memory_change_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("batch_hash", sa.String(64), nullable=False),
        sa.Column("entry_id", sa.String(64), nullable=False),
        sa.Column("op", sa.String(16), nullable=False),
        sa.Column("before", sa.Text(), nullable=False, server_default=""),
        sa.Column("after", sa.Text(), nullable=False, server_default=""),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_v3_change_log_batch", "agent_v3_memory_change_log", ["batch_hash"])

    op.create_table(
        "agent_v3_memory_batches",
        sa.Column("batch_hash", sa.String(64), primary_key=True),
        sa.Column("receipt", postgresql.JSONB(), nullable=False),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )

    op.create_table(
        "agent_v3_memory_drafts",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("layer", sa.String(16), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("evidence", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )


def downgrade() -> None:
    op.drop_table("agent_v3_memory_drafts")
    op.drop_table("agent_v3_memory_batches")
    op.drop_index("ix_v3_change_log_batch", table_name="agent_v3_memory_change_log")
    op.drop_table("agent_v3_memory_change_log")
    op.drop_table("agent_v3_memory_corrections")
    op.drop_index("ix_v3_memory_active", table_name="agent_v3_memory_entries")
    op.drop_table("agent_v3_memory_state")
    op.drop_table("agent_v3_memory_entries")
