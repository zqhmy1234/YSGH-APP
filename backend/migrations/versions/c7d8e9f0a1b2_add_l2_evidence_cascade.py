"""profile_l2_evidence 外键补 ON DELETE CASCADE（2026-08-26 集成对齐）

背景：Wave 3 Agent I（B1 画像）标注核心以原生 SQL 写 profile_l2_evidence；
基线迁移 431bcaa8bd54 的 FK（user_id→users.id）无级联——硬删用户
（测试 fixture teardown / 未来 30 天 purge）会被 evidence 行阻断（FK 报错）。
I 已在 dev 库手动补建（confdeltype='c'），本迁移对齐 CI/新库。
幂等：约束同名，dev 上重放 = drop 后原样重建；IF EXISTS 兼容手动库。

Revision ID: c7d8e9f0a1b2
Revises: b0b1c2d3e4f5
Create Date: 2026-08-26
"""
from collections.abc import Sequence

from alembic import op

revision: str = "c7d8e9f0a1b2"
down_revision: str | Sequence[str] | None = "b0b1c2d3e4f5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONSTRAINT = "profile_l2_evidence_user_id_fkey"


def upgrade() -> None:
    op.execute(
        f"ALTER TABLE profile_l2_evidence "
        f"DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"
    )
    op.execute(
        f"ALTER TABLE profile_l2_evidence "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        f"FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE"
    )


def downgrade() -> None:
    op.execute(
        f"ALTER TABLE profile_l2_evidence "
        f"DROP CONSTRAINT IF EXISTS {_CONSTRAINT}"
    )
    op.execute(
        f"ALTER TABLE profile_l2_evidence "
        f"ADD CONSTRAINT {_CONSTRAINT} "
        f"FOREIGN KEY (user_id) REFERENCES users(id)"
    )
