"""C 类悬账：event_edit_log.id 序列漂移根治（幂等 guard）

Revision ID: c8d9e0f1a2b3
Revises: b2a3c4d5e6f7
Create Date: 2026-09-09 00:00:00.000000

诊断结论（见本批任务报告）：
- backend/sql/schema.sql L192 定义 `id bigserial PRIMARY KEY`——建库源本身正确
  （bigserial 等价于 CREATE SEQUENCE + nextval 默认值）。
- alembic 链基线 431bcaa8bd54 对 event_edit_log 只做 alter_column/drop_constraint，
  **从不 create_table**（该表假定已由 schema.sql 预建）→ 迁移链不产出序列，
  但也不破坏既有 bigserial 默认值。
- 因此漂移根因＝「schema.sql 建的库被后续手工 DDL（或历史遗留建表路径）拆掉了序列
  默认值」，非迁移链缺陷。真实事故实录见 docs 台账与 tests/test_ab_scenarios.py 头注：
  本测试库曾出现 id 列无 default 无序列 → merge/split/confirm 一律 IntegrityError，
  2026-09-08 已手工补建 event_edit_log_id_seq + SET DEFAULT（本地已修）。
- 风险＝任何环境复现该漂移后无法自愈。修法＝补本幂等 guard migration：
  CREATE SEQUENCE IF NOT EXISTS + SET DEFAULT nextval + ALTER SEQUENCE OWNED BY
  （归位为 serial 语义，随表删除级联）+ setval 对齐 max(id)（防空库/存量手工 id 撞主键）。
  全语句幂等，任何路径建库后 `alembic upgrade head` 都能把该列拉回 bigserial 等价态。
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c8d9e0f1a2b3"
down_revision: str | Sequence[str] | None = "b2a3c4d5e6f7"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """幂等自愈：序列补建/默认值挂回/值对齐（IF NOT EXISTS + 条件 setval）"""
    # ① 序列存在性（漂移库常连序列都丢了）；bigserial 隐式序列命名即此名
    op.execute("CREATE SEQUENCE IF NOT EXISTS event_edit_log_id_seq")
    # ② 归属关系复位（serial 语义：表删序列随删、pg_depend 依赖登记）
    op.execute(
        "ALTER SEQUENCE event_edit_log_id_seq OWNED BY event_edit_log.id"
    )
    # ③ 列默认值挂回（SET DEFAULT 天然幂等，重复执行无副作用）
    op.execute(
        "ALTER TABLE event_edit_log "
        "ALTER COLUMN id SET DEFAULT nextval('event_edit_log_id_seq'::regclass)"
    )
    # ④ 序列推进到 max(id)，防下一个 nextval 撞存量手工 id（主键冲突）。
    #    greatest(...,1)：setval 拒绝 0/NULL（空库 max=NULL）；is_called=true
    #    使 nextval 从 max+1 起。仅在落后时执行，避免高水位回拨。
    op.execute(
        "SELECT setval('event_edit_log_id_seq', "
        "greatest((SELECT COALESCE(max(id), 0) FROM event_edit_log), 1), true) "
        "FROM event_edit_log_id_seq "
        "WHERE last_value < "
        "greatest((SELECT COALESCE(max(id), 0) FROM event_edit_log), 1)"
    )


def downgrade() -> None:
    """Downgrade schema.

    有意 no-op：默认值/序列是本 guard 意图修复的目标态（serial 语义归位），
    回滚等于恢复故障，无还原语义。
    """
