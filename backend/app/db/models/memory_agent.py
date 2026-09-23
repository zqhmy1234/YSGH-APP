"""Agent 记忆域 ORM 镜像（memory_categories / memories / knowledge_collections）

为什么在 backend 里再声明一份（**镜像**，不是本仓业务表）：
  这三表由开发部同事的 Agent 交付包使用（原在 Supabase），经用户拍板改由**我们自己的 Postgres**
  承载，故表结构由本仓 alembic 建（迁移 `a4b5c6d7e8f9`）。
  但**表若只存在于迁移、不进 `Base.metadata`**，`alembic revision --autogenerate` 会把
  "库里有、metadata 没有"的表判为多余并生成 **DROP TABLE** —— 那是能删数据的静默事故。
  故在此镜像声明，使 metadata 与库一致（本仓既有纪律："ORM 为唯一权威，Alembic check 零漂移"）。

**两处必须同时改**：本文件与迁移 `backend/migrations/versions/a4b5c6d7e8f9_add_agent_memory_tables.py`
（另 Agent 侧另有等价声明 `agent/src/storage/database/shared/model.py`，由 backend 测试
`test_agent_schema_alignment.py` 做漂移守卫）。

命名差异（刻意）：表名沿用上游（`memory_categories` 等，Agent 侧 SQL 依赖），类名按本仓风格取单数。
"""
from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy import text as sa_text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class MemoryCategory(Base):
    """记忆分类字典（Agent 记忆域的 L1 分类）。"""

    __tablename__ = "memory_categories"
    __table_args__ = (
        UniqueConstraint("name", name="memory_categories_name_key"),
        Index("ix_memory_categories_name", "name"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment="分类名称")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True, comment="分类描述")
    icon: Mapped[str | None] = mapped_column(String(50), nullable=True, comment="图标标识")


class Memory(Base):
    """个人记忆（主观经历/想法/情绪/计划/反思）—— Agent 侧 memories 表。"""

    __tablename__ = "memories"
    __table_args__ = (
        Index("ix_memories_category_id", "category_id"),
        Index("ix_memories_created_at", "created_at"),
        Index("ix_memories_importance", "importance"),
        Index("ix_memories_mood", "mood"),
        Index("ix_memories_user_id", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="记忆原始内容")
    importance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("3"), comment="重要程度1-5"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="用户ID（用于用户数据隔离）")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="AI生成的摘要")
    category_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("memory_categories.id", name="memories_category_id_fkey"), nullable=True
    )
    tags: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="标签列表（旧字段，已废弃）")
    mood: Mapped[str | None] = mapped_column(String(20), nullable=True, comment="情绪状态")
    location: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="地点")
    people: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="相关人物列表")
    content_format: Mapped[str | None] = mapped_column(
        String(20), server_default=sa_text("'text'"), nullable=True, comment="内容形式：text/image/voice/link"
    )
    topics: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="L3主题领域列表")
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class KnowledgeCollection(Base):
    """知识收藏（外部文章/帖子/观点/数据/图片）—— Agent 侧 knowledge_collections 表。"""

    __tablename__ = "knowledge_collections"
    __table_args__ = (
        Index("ix_knowledge_user_id", "user_id"),
        Index("ix_knowledge_content_type", "content_type"),
        Index("ix_knowledge_created_at", "created_at"),
        Index("ix_knowledge_importance", "importance"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String, nullable=True, comment="用户ID")
    content: Mapped[str] = mapped_column(Text, nullable=False, comment="内容")
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment="内容类型：article/post/opinion/data/visual/other"
    )
    summary: Mapped[str | None] = mapped_column(Text, nullable=True, comment="AI生成的摘要")
    title: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="标题")
    author: Mapped[str | None] = mapped_column(String(200), nullable=True, comment="作者")
    source: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="来源（URL/平台/出处）")
    source_date: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, comment="来源发布日期"
    )
    keywords: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="关键词列表")
    topics: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="主题分类")
    image_url: Mapped[str | None] = mapped_column(String(500), nullable=True, comment="图片URL（视觉信息）")
    related_memory_ids: Mapped[dict | None] = mapped_column(JSON, nullable=True, comment="关联的主观记忆ID列表")
    importance: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=sa_text("3"), comment="重要程度1-5"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=sa_text("now()")
    )
    content_format: Mapped[str | None] = mapped_column(
        String(20), server_default=sa_text("'text'"), nullable=True, comment="内容形式：text/image/voice/link"
    )
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
