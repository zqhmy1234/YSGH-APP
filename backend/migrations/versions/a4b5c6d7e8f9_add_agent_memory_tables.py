"""Agent 记忆域三表：memory_categories / memories / knowledge_collections

Revision ID: a4b5c6d7e8f9
Revises: b8c9d0e1f2a3
Create Date: 2026-09-23 21:00:00.000000

背景（AG3 · Agent 接线批）：
  开发部同事交付的 Agent 项目原先把这三表放在 **Supabase（PostgREST）**。用户拍板改为
  **落在我们自己的 Postgres**，故由本仓 alembic 统一建表（单一迁移链，避免两套库两套口径）。

规格来源：`agent/src/storage/database/shared/model.py`（上游 ORM 声明）；本迁移与其**逐列对齐**
（含 JSON 列、TIMESTAMPTZ、server_default）。**镜像 ORM** 见 `app/db/models/memory_agent.py` ——
两处必须同时改，否则 `alembic revision --autogenerate` 会把"库里有、metadata 没有"的表判为多余而生成
DROP TABLE（已在本文件头显式提醒）。

不建的表（刻意）：
  - `health_check`：上游 Coze 运行时的健康表，去 Coze 后无用途
  - `pg_stat_statements*`：Postgres 扩展视图，非业务表
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a4b5c6d7e8f9"
# 父节点取自 `alembic heads` 的实际 tip（b8c9d0e1f2a3），**不是**文件名/时间看起来最新的
# f2a3b4c5d6e7 —— 后者只是祖先（链：f2a3b4c5d6e7 → b2a3c4d5e6f7 → … → c8d9e0f1a2b3 → b8c9d0e1f2a3）。
# 首次误用祖先当父节点 ⇒ 直接造出第二个 head，`alembic upgrade head` 报
# "Multiple head revisions are present"（教训：迁移父节点只能以 heads 判据决定）。
down_revision: str | Sequence[str] | None = "b8c9d0e1f2a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    # ── memory_categories：分类字典（记忆的 L1 分类）──
    op.create_table(
        "memory_categories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=50), nullable=False, comment="分类名称"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("description", sa.Text(), nullable=True, comment="分类描述"),
        sa.Column("icon", sa.String(length=50), nullable=True, comment="图标标识"),
        sa.PrimaryKeyConstraint("id", name="memory_categories_pkey"),
        sa.UniqueConstraint("name", name="memory_categories_name_key"),
    )
    op.create_index("ix_memory_categories_name", "memory_categories", ["name"], unique=False)

    # ── memories：个人记忆（主观经历/想法/情绪/计划/反思）──
    op.create_table(
        "memories",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False, comment="记忆原始内容"),
        sa.Column(
            "importance",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
            comment="重要程度1-5",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=True, comment="用户ID（用于用户数据隔离）"),
        sa.Column("summary", sa.Text(), nullable=True, comment="AI生成的摘要"),
        sa.Column("category_id", sa.Integer(), nullable=True, comment="分类ID"),
        sa.Column("tags", sa.JSON(), nullable=True, comment="标签列表（旧字段，已废弃）"),
        sa.Column("mood", sa.String(length=20), nullable=True, comment="情绪状态"),
        sa.Column("location", sa.String(length=200), nullable=True, comment="地点"),
        sa.Column("people", sa.JSON(), nullable=True, comment="相关人物列表"),
        sa.Column(
            "content_format",
            sa.String(length=20),
            server_default=sa.text("'text'"),
            nullable=True,
            comment="内容形式：text/image/voice/link",
        ),
        sa.Column("topics", sa.JSON(), nullable=True, comment="L3主题领域列表"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["category_id"],
            ["memory_categories.id"],
            name="memories_category_id_fkey",
        ),
        sa.PrimaryKeyConstraint("id", name="memories_pkey"),
    )
    op.create_index("ix_memories_category_id", "memories", ["category_id"], unique=False)
    op.create_index("ix_memories_created_at", "memories", ["created_at"], unique=False)
    op.create_index("ix_memories_importance", "memories", ["importance"], unique=False)
    op.create_index("ix_memories_mood", "memories", ["mood"], unique=False)
    op.create_index("ix_memories_user_id", "memories", ["user_id"], unique=False)

    # ── knowledge_collections：知识收藏（外部文章/帖子/观点/数据/图片）──
    op.create_table(
        "knowledge_collections",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(), nullable=True, comment="用户ID"),
        sa.Column("content", sa.Text(), nullable=False, comment="内容"),
        sa.Column(
            "content_type",
            sa.String(length=50),
            nullable=False,
            comment="内容类型：article/post/opinion/data/visual/other",
        ),
        sa.Column("summary", sa.Text(), nullable=True, comment="AI生成的摘要"),
        sa.Column("title", sa.String(length=500), nullable=True, comment="标题"),
        sa.Column("author", sa.String(length=200), nullable=True, comment="作者"),
        sa.Column("source", sa.String(length=500), nullable=True, comment="来源（URL/平台/出处）"),
        sa.Column("source_date", sa.DateTime(timezone=True), nullable=True, comment="来源发布日期"),
        sa.Column("keywords", sa.JSON(), nullable=True, comment="关键词列表"),
        sa.Column("topics", sa.JSON(), nullable=True, comment="主题分类"),
        sa.Column("image_url", sa.String(length=500), nullable=True, comment="图片URL（视觉信息）"),
        sa.Column("related_memory_ids", sa.JSON(), nullable=True, comment="关联的主观记忆ID列表"),
        sa.Column(
            "importance",
            sa.Integer(),
            server_default=sa.text("3"),
            nullable=False,
            comment="重要程度1-5",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "content_format",
            sa.String(length=20),
            server_default=sa.text("'text'"),
            nullable=True,
            comment="内容形式：text/image/voice/link",
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="knowledge_collections_pkey"),
    )
    op.create_index("ix_knowledge_user_id", "knowledge_collections", ["user_id"], unique=False)
    op.create_index("ix_knowledge_content_type", "knowledge_collections", ["content_type"], unique=False)
    op.create_index("ix_knowledge_created_at", "knowledge_collections", ["created_at"], unique=False)
    op.create_index("ix_knowledge_importance", "knowledge_collections", ["importance"], unique=False)


def downgrade() -> None:
    """Downgrade schema."""
    # 先删索引再删表（memories 有指向 memory_categories 的外键，顺序不能反）
    op.drop_index("ix_knowledge_importance", table_name="knowledge_collections")
    op.drop_index("ix_knowledge_created_at", table_name="knowledge_collections")
    op.drop_index("ix_knowledge_content_type", table_name="knowledge_collections")
    op.drop_index("ix_knowledge_user_id", table_name="knowledge_collections")
    op.drop_table("knowledge_collections")

    op.drop_index("ix_memories_user_id", table_name="memories")
    op.drop_index("ix_memories_mood", table_name="memories")
    op.drop_index("ix_memories_importance", table_name="memories")
    op.drop_index("ix_memories_created_at", table_name="memories")
    op.drop_index("ix_memories_category_id", table_name="memories")
    op.drop_table("memories")

    op.drop_index("ix_memory_categories_name", table_name="memory_categories")
    op.drop_table("memory_categories")
