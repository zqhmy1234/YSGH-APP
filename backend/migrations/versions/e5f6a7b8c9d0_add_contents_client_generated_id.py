"""add contents.client_generated_id (R4#4 创建端点幂等键)

Revision ID: e5f6a7b8c9d0
Revises: d3e4f5a6b7c8
Create Date: 2026-08-27 20:00:00.000000

重构侦察 R4-P1#4：POST /contents（text/article）无幂等键，双击/重试即重复入库。
新增客户端生成的幂等键 client_generated_id：(user_id, client_generated_id) 部分唯一索引
（PG 多 NULL 不冲突 → 仅非空参与），photo/voice 既有幂等（perceptual_hash 409 /
cos_key）保留为兜底。模板参照 uq_events_user_client_event（a1b2c3d4e5f6）。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e5f6a7b8c9d0"
down_revision: str | Sequence[str] | None = "d3e4f5a6b7c8"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "contents", sa.Column("client_generated_id", sa.String(length=64), nullable=True)
    )
    op.create_index(
        "uq_contents_user_client_generated_id",
        "contents",
        ["user_id", "client_generated_id"],
        unique=True,
        postgresql_where=sa.text("client_generated_id IS NOT NULL"),
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index(
        "uq_contents_user_client_generated_id",
        table_name="contents",
        postgresql_where=sa.text("client_generated_id IS NOT NULL"),
    )
    op.drop_column("contents", "client_generated_id")
