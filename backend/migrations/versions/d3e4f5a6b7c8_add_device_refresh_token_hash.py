"""TD-P3 M6（审查中危/低危）：devices 表 refresh_token 哈希化

- 新增 refresh_token_hash（SHA-256 存储，DB 泄漏不可直接复用 30 天会话）
- 新增 refresh_rotated_at（最后轮换时间，可观测/后续过期策略依据）
- 保留 refresh_token 明文列（迁移期兼容存量行；登录即覆写清空，无需回填迁移）

幂等：add_column 本身幂等（重复执行报列已存在则由 alembic 版本表防重）；downgrade 对称删除。

Revision ID: d3e4f5a6b7c8
Revises: a7b8c9d0e1f2
Create Date: 2026-08-26
"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d3e4f5a6b7c8"
down_revision: str | Sequence[str] | None = "a7b8c9d0e1f2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("devices", sa.Column("refresh_token_hash", sa.String(), nullable=True))
    op.add_column(
        "devices", sa.Column("refresh_rotated_at", sa.DateTime(timezone=True), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("devices", "refresh_rotated_at")
    op.drop_column("devices", "refresh_token_hash")
