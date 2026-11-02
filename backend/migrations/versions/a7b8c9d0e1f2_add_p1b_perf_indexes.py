"""P1B 性能索引（S6-4/S6-6 · 2026-08-26）

补齐缺失索引，消除三类热点扫描：
  - deleted_logs(cleanup_status, deleted_at)：30 天软删除清理对账扫描
  - offline_queue(user_id, id)：增量拉取游标（pull_changes since > cursor）
  - messages(user_id, id)：消息按用户+id 分页/游标
  - profile_l2_evidence(user_id, dimension)：画像证据溯源查询（S6-6）

幂等：IF NOT EXISTS 兼容本地手动库；downgrade 对称删除。

Revision ID: a7b8c9d0e1f2
Revises: c7d8e9f0a1b2
Create Date: 2026-08-26
"""
from collections.abc import Sequence

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: str | Sequence[str] | None = "c7d8e9f0a1b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE INDEX IF NOT EXISTS idx_deleted_logs_cleanup ON deleted_logs (cleanup_status, deleted_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_offline_queue_user_id ON offline_queue (user_id, id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_messages_user_id ON messages (user_id, id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_l2_evidence_user_dim ON profile_l2_evidence (user_id, dimension)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_deleted_logs_cleanup")
    op.execute("DROP INDEX IF EXISTS idx_offline_queue_user_id")
    op.execute("DROP INDEX IF EXISTS idx_messages_user_id")
    op.execute("DROP INDEX IF EXISTS idx_l2_evidence_user_dim")
