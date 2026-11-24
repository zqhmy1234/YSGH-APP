"""B4 回响：dismiss 幂等的部分唯一索引

Revision ID: b4e5c6d7f8a9
Revises: e7b8c9d0e1f2

背景（2026-11-24 四路审判 P1）：
  echo_history 现有部分唯一索引 uq_echo_history_daily 的 WHERE 条件是
  `action IS DISTINCT FROM 'dismiss'`，明确把 dismiss 行排除——因此 dismiss
  没有任何 DB 约束兜底。API 层"先 SELECT count 再 INSERT"是 check-then-act，
  并发两次请求都看到 count=0 会插入两条，"重复划掉不叠加记录"的承诺是假的。

本迁移新增 (user_id, fingerprint) WHERE action='dismiss' 的部分唯一索引，
让并发重复 dismiss 在 DB 层冲突（API 已捕获 IntegrityError → 视为已划掉）。
不改动现有 uq_echo_history_daily，不影响每日上限的并发兜底。
"""
from alembic import op
import sqlalchemy as sa

revision: str = "b4e5c6d7f8a9"
down_revision: str | None = "e7b8c9d0e1f2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 历史数据若已有重复 dismiss 行，先去重（保留 id 最小的一条），否则建索引失败。
    op.execute(
        """
        DELETE FROM echo_history a USING echo_history b
        WHERE a.action = 'dismiss' AND b.action = 'dismiss'
          AND a.user_id = b.user_id AND a.fingerprint = b.fingerprint
          AND a.fingerprint IS NOT NULL AND a.id > b.id
        """
    )
    op.create_index(
        "uq_echo_history_dismiss",
        "echo_history",
        ["user_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("action = 'dismiss'"),
    )


def downgrade() -> None:
    op.drop_index("uq_echo_history_dismiss", table_name="echo_history")
