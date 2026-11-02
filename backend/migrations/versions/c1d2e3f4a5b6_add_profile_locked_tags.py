"""add user_profile.locked_tags（用户锁定标签，AI 不再自动写回）

B1:139 设计口径：对话式修改/删除 → 「删除 + 用户锁定，LLM 不再自动写回」。
此前客户端 `portrait_locked_tags` 只写本机 storage，后端完全不知情 ⇒ 画像重建时
AI 会把用户删掉/锁定的标签写回来（锁定是空承诺）。本列是该集合的**唯一真源**
（客户端 storage 降级为离线兜底缓存），由 `POST/PUT /api/v1/profile/locked-tags`
维护，`services/profile_annotator.apply_annotation` 在写回前按本集合跳过。

复用 `user_profile` 主表加 JSONB 列（不新建表）：集合很小（≤ MAX_TAGS），
且总随画像一起读，无独立查询需求。

revision: c1d2e3f4a5b6
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "c1d2e3f4a5b6"
down_revision = "b3a1c2d3e4f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profile",
        sa.Column(
            "locked_tags",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            # 存量行补默认空数组：NOT NULL 才能让读侧不必到处判 None
            server_default=sa.text("'[]'::jsonb"),
        ),
    )


def downgrade() -> None:
    # 只丢列，不试图把锁定标签还原到别处：本列是唯一真源，降级即放弃该能力
    # （客户端读到接口 404 会回退本地兜底缓存，用户可见的锁定不会凭空消失）。
    op.drop_column("user_profile", "locked_tags")
