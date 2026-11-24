"""B6 信任域：用途授权 / 保险箱 / 安全审计三张表

Revision ID: b6c7d8e9f0a1
Revises: b4e5c6d7f8a9

用途授权：consent_grant（scope 白名单在服务层，默认无行=未授权）
保险箱　：vault_entry（内容级覆盖层，不加密原文本体，锁访问入口）
安全审计：audit_log（关键动作留痕，用户可查自己的记录）
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "b6c7d8e9f0a1"
down_revision: str | None = "b4e5c6d7f8a9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "consent_grant",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("scope", sa.String(48), nullable=False),
        sa.Column("state", sa.String(16), server_default=sa.text("'granted'"), nullable=False),
        sa.Column("granted_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("user_id", "scope", name="uq_consent_grant_user_scope"),
    )
    op.create_index("ix_consent_grant_user_id", "consent_grant", ["user_id"])

    op.create_table(
        "vault_entry",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("content_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("granted_roles", postgresql.JSONB(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("unlocked_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("user_id", "content_id", name="uq_vault_entry_user_content"),
    )
    op.create_index("ix_vault_entry_user_id", "vault_entry", ["user_id"])
    op.create_index("ix_vault_entry_content_id", "vault_entry", ["content_id"])
    op.create_index("ix_vault_entry_user_created", "vault_entry", ["user_id", "created_at"])

    op.create_table(
        "audit_log",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=False),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("action", sa.String(48), nullable=False),
        sa.Column("target", sa.String(128), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_audit_log_user_id", "audit_log", ["user_id"])
    op.create_index("ix_audit_log_action", "audit_log", ["action"])
    op.create_index("ix_audit_log_user_created", "audit_log", ["user_id", "created_at"])


def downgrade() -> None:
    op.drop_table("audit_log")
    op.drop_table("vault_entry")
    op.drop_table("consent_grant")
