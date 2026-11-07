"""add events.client_event_id (S-SY-1 端侧事件幂等键)

Revision ID: a1b2c3d4e5f6
Revises: 225d7b006ab3
Create Date: 2026-08-24 18:40:00.000000

S-SY-1（B3-6 端侧 L0/L1 真值）：POST /api/v1/events/sync 幂等依赖
(client_user_id, client_event_id) 部分唯一索引（PG 多 NULL 不冲突 → 仅非空参与）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a1b2c3d4e5f6"
down_revision: str | Sequence[str] | None = "225d7b006ab3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("events", sa.Column("client_event_id", sa.String(length=64), nullable=True))
    op.create_index(
        "uq_events_user_client_event",
        "events",
        ["user_id", "client_event_id"],
        unique=True,
        postgresql_where=sa.text("client_event_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_events_user_client_event",
        table_name="events",
        postgresql_where=sa.text("client_event_id IS NOT NULL"),
    )
    op.drop_column("events", "client_event_id")
