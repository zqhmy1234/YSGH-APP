"""B6 保险箱令牌轮次：去掉全表唯一约束，改部分唯一索引

Revision ID: b6d9e0f1a2b3
Revises: b6c7d8e9f0a1

审判发现：解锁原为物理删除 VaultEntry 行，令牌是确定性 HMAC(user/content)，
旧令牌永久可重放；且解锁后重新上锁会复用同一行，无法区分轮次。
改为：解锁标记 unlocked_at（不删行），重新上锁建新行（新 id = 新轮次），
令牌带 generation + 5 分钟时效。约束改为只约束 active 行，允许历史轮次共存。
"""
from alembic import op
import sqlalchemy as sa

revision: str = "b6d9e0f1a2b3"
down_revision: str | None = "b6c7d8e9f0a1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_vault_entry_user_content", "vault_entry", type_="unique")
    op.create_index(
        "uq_vault_entry_active", "vault_entry", ["user_id", "content_id"],
        unique=True, postgresql_where=sa.text("unlocked_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("uq_vault_entry_active", table_name="vault_entry")
    op.create_unique_constraint(
        "uq_vault_entry_user_content", "vault_entry", ["user_id", "content_id"]
    )
