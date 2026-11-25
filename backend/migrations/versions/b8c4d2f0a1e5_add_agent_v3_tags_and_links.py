"""agent-v3 normalized tags and memory<->collection links.

Revision ID: b8c4d2f0a1e5
Revises: a7f3c2e91b44

Why separate tables: entries remain the append-only ledger; tags are derived
labels that can be rebuilt, and links are a relation. This avoids ALTERing the
ledger's text/evidence columns and keeps a re-tag job possible.
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "b8c4d2f0a1e5"
down_revision: Union[str, Sequence[str], None] = "a7f3c2e91b44"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_v3_episode_tags",
        sa.Column("episode_id", sa.String(100), primary_key=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("library", sa.String(16), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(16), nullable=False, server_default=""),
        sa.Column("mood", sa.String(16), nullable=False, server_default=""),
        sa.Column("topics", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("content_format", sa.String(16), nullable=False, server_default="text"),
        sa.Column("image_route", sa.String(16), nullable=False, server_default=""),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "agent_v3_memory_tags",
        sa.Column("entry_id", sa.String(64), primary_key=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("library", sa.String(16), nullable=False),
        sa.Column("category", sa.String(16), nullable=False, server_default=""),
        sa.Column("content_type", sa.String(16), nullable=False, server_default=""),
        sa.Column("mood", sa.String(16), nullable=False, server_default=""),
        sa.Column("topics", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("content_format", sa.String(16), nullable=False, server_default="text"),
        sa.Column("image_route", sa.String(16), nullable=False, server_default=""),
        sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_table(
        "agent_v3_links",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("person_id", sa.String(64), nullable=False, index=True),
        sa.Column("memory_id", sa.String(64), nullable=False),
        sa.Column("collection_id", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("person_id", "memory_id", "collection_id", name="uq_v3_link_triple"),
    )


def downgrade() -> None:
    op.drop_table("agent_v3_links")
    op.drop_table("agent_v3_memory_tags")
    op.drop_table("agent_v3_episode_tags")
