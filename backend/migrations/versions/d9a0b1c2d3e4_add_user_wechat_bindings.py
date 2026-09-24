"""D09-1：重建 user_wechat_bindings（微信绑定表，F6 主链载体）

Revision ID: d9a0b1c2d3e4
Revises: b5c6d7e8f9a0
Create Date: 2026-09-25 00:00:00.000000

## 为什么需要这条迁移

`user_wechat_bindings` 自 `backend/sql/schema.sql:37` 起就存在（注释「一个 unionid
多 openid」），但**从未映射到 ORM**，于是 baseline 迁移 `431bcaa8bd54` 按
"ORM 未映射的遗留空表" 把它 **DROP 掉**（见该迁移 `_LEGACY_EMPTY_TABLES`）。

后果（域⑨ 深审 D09-1 · P0）：`api/wechat.py` 的回调**无 user_id 可查**，只能
`process_incoming(db, msg)` ⇒ 企微回调整条 F6 主链（收 → 下载媒体 → 建 Content →
入管线 → 产记忆）**不通**，只落 `wechat_messages` 一行。

本迁移补建该表（ORM 侧同步补 `db/models/wechat.py::UserWechatBinding`），
使"绑定存在 ⇒ 回调可解析 user_id ⇒ 主链贯通"。

## 为什么用幂等 SQL 而非 op.create_table

本仓有**两条建库路径**：`backend/sql/schema.sql`（CI 建库快照）与 alembic 迁移链。
`schema.sql` **本来就含这张表** ⇒ 若用 `op.create_table` 会在该路径上撞
"table already exists"。故沿用先例（`c8d9e0f1a2b3` guard 迁移）用
`CREATE TABLE IF NOT EXISTS` + 条件补索引，任何路径跑 `alembic upgrade head`
都收敛到同一目标态。
"""
from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d9a0b1c2d3e4"
down_revision: str | Sequence[str] | None = "b5c6d7e8f9a0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """幂等建表（与 schema.sql L37-43 逐列一致）：openid 唯一、user_id 外键 users"""
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS user_wechat_bindings (
            id       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            user_id  uuid NOT NULL REFERENCES users(id),
            openid   text NOT NULL UNIQUE,
            channel  text NOT NULL,
            bound_at timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    # 刻意不加 user_id 索引：schema.sql（另一条建库路径）无该索引，加了会与
    # check_schema_drift 的两侧对拍产生假漂移。表体量极小（每用户每渠道一行），
    # 唯一索引在 openid（主查方向），反查 user_id 走顺序扫描即可。


def downgrade() -> None:
    """回滚 = 删表（与 baseline 的历史状态一致：表不存在）"""
    op.execute("DROP TABLE IF EXISTS user_wechat_bindings")
