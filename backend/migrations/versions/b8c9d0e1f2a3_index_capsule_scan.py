"""性能优化波 C：capsule_scan 全局轮询复合偏索引

Revision ID: b8c9d0e1f2a3
Revises: c8d9e0f1a2b3
Create Date: 2026-09-10 00:00:00.000000

审计结论（2026-09-10 后端重构波 C 索引审计）：
- workers/capsule_scan.py 每轮全局扫 `status='sealed' AND open_at <= now`
  （非 user_id 过滤；到期推送独立流程最外层编排者）。
- capsules.status 仅有单列索引（models/capsule.py），但 sealed 是热态
  （存量胶囊绝大多数处于 sealed）→ 单列索引选择性差，PG 大概率放弃走索引、
  退化为全表扫 + 过滤；随胶囊存量增长每次轮询成本线性上升。
- 复合偏索引 (status, open_at) WHERE status='sealed'：扫描直接定位 sealed 段并
  按 open_at 有序命中；partial 只收录 sealed 行，体积最小、sealed→due/opened
  流转时行自动移出索引，天然保持精简。
- 本批 61 处 .all() 索引审计中唯一真正的缺口（其余判定为健康有界
  rn 截断/in_/group_by/limit 或架构性全量如 reconcile 对账）。

幂等：CREATE INDEX IF NOT EXISTS（schema.sql 预建库 / alembic 链建库双路径均可执行，
重复执行无害）。
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8c9d0e1f2a3"
down_revision: str | Sequence[str] | None = "c8d9e0f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_capsules_sealed_openat "
        "ON capsules (status, open_at) WHERE status = 'sealed'"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_capsules_sealed_openat")
