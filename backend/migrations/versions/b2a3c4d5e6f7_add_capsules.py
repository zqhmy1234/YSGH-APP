"""BA2 字段补齐批：capsules 表（时间胶囊）

Revision ID: b2a3c4d5e6f7
Revises: f2a3b4c5d6e7
Create Date: 2026-09-07 12:00:00.000000

产品语义（2026-09-02 峰宝拍板：全量含到期推送）：
用户选一条记忆 + 未来时间封存；到期可开启回看 + 站内消息提醒。
status 流转：sealed → due（扫描置位，消息幂等键）→ opened。
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "b2a3c4d5e6f7"
down_revision: str | Sequence[str] | None = "f2a3b4c5d6e7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "capsules",
        sa.Column("id", UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", UUID(as_uuid=False), nullable=False),
        sa.Column("content_id", UUID(as_uuid=False), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "sealed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("open_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.String(),
            nullable=False,
            server_default="sealed",
            comment="sealed/due/opened",
        ),
    )
    op.create_index("ix_capsules_user_id", "capsules", ["user_id"])
    op.create_index("ix_capsules_status", "capsules", ["status"])


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_capsules_status", table_name="capsules")
    op.drop_index("ix_capsules_user_id", table_name="capsules")
    op.drop_table("capsules")
