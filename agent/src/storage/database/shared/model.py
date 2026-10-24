"""ORM 声明（三表）——**去 Coze 改造点**：Base 由原 `coze_coding_dev_sdk.database.Base` 换为
本项目自有基类（`storage/database/base.py`，见 agent/UPSTREAM.md 四-2）。

施工要点（AG3）：这里的列定义可直接作为 alembic 迁移的规格；三表由 backend 的迁移统一创建
（本服务不自行建表），user_id 口径与 backend 保持一致。
"""
import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Double,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import OID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from storage.database.base import Base


class HealthCheck(Base):
    __tablename__ = 'health_check'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='health_check_pkey'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True), server_default=text('now()'))


class MemoryCategories(Base):
    __tablename__ = 'memory_categories'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='memory_categories_pkey'),
        UniqueConstraint('name', name='memory_categories_name_key'),
        Index('ix_memory_categories_name', 'name')
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(50), nullable=False, comment='分类名称')
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    description: Mapped[str | None] = mapped_column(Text, comment='分类描述')
    icon: Mapped[str | None] = mapped_column(String(50), comment='图标标识')

    memories: Mapped[list['Memories']] = relationship('Memories', back_populates='category')


t_pg_stat_statements = Table(
    'pg_stat_statements', Base.metadata,
    Column('userid', OID),
    Column('dbid', OID),
    Column('toplevel', Boolean),
    Column('queryid', BigInteger),
    Column('query', Text),
    Column('plans', BigInteger),
    Column('total_plan_time', Double(53)),
    Column('min_plan_time', Double(53)),
    Column('max_plan_time', Double(53)),
    Column('mean_plan_time', Double(53)),
    Column('stddev_plan_time', Double(53)),
    Column('calls', BigInteger),
    Column('total_exec_time', Double(53)),
    Column('min_exec_time', Double(53)),
    Column('max_exec_time', Double(53)),
    Column('mean_exec_time', Double(53)),
    Column('stddev_exec_time', Double(53)),
    Column('rows', BigInteger),
    Column('shared_blks_hit', BigInteger),
    Column('shared_blks_read', BigInteger),
    Column('shared_blks_dirtied', BigInteger),
    Column('shared_blks_written', BigInteger),
    Column('local_blks_hit', BigInteger),
    Column('local_blks_read', BigInteger),
    Column('local_blks_dirtied', BigInteger),
    Column('local_blks_written', BigInteger),
    Column('temp_blks_read', BigInteger),
    Column('temp_blks_written', BigInteger),
    Column('shared_blk_read_time', Double(53)),
    Column('shared_blk_write_time', Double(53)),
    Column('local_blk_read_time', Double(53)),
    Column('local_blk_write_time', Double(53)),
    Column('temp_blk_read_time', Double(53)),
    Column('temp_blk_write_time', Double(53)),
    Column('wal_records', BigInteger),
    Column('wal_fpi', BigInteger),
    Column('wal_bytes', Numeric),
    Column('jit_functions', BigInteger),
    Column('jit_generation_time', Double(53)),
    Column('jit_inlining_count', BigInteger),
    Column('jit_inlining_time', Double(53)),
    Column('jit_optimization_count', BigInteger),
    Column('jit_optimization_time', Double(53)),
    Column('jit_emission_count', BigInteger),
    Column('jit_emission_time', Double(53)),
    Column('jit_deform_count', BigInteger),
    Column('jit_deform_time', Double(53)),
    Column('stats_since', DateTime(True)),
    Column('minmax_stats_since', DateTime(True))
)


t_pg_stat_statements_info = Table(
    'pg_stat_statements_info', Base.metadata,
    Column('dealloc', BigInteger),
    Column('stats_reset', DateTime(True))
)


class Memories(Base):
    __tablename__ = 'memories'
    __table_args__ = (
        ForeignKeyConstraint(['category_id'], ['memory_categories.id'], name='memories_category_id_fkey'),
        PrimaryKeyConstraint('id', name='memories_pkey'),
        Index('ix_memories_category_id', 'category_id'),
        Index('ix_memories_created_at', 'created_at'),
        Index('ix_memories_importance', 'importance'),
        Index('ix_memories_mood', 'mood'),
        Index('ix_memories_user_id', 'user_id'),
        # ── 判重幂等：部分唯一索引（2026-10-06 迁移 f2c3d4e5a6b7）──
        # ⚠️ 必须在此声明，否则 `alembic check` / `--autogenerate` 会把它判为
        # "库里有、metadata 没有" 的**多余索引**并生成 `DROP INDEX`（实测已复现）。
        # 谓词必须与迁移里的 `WHERE` **逐字一致**，否则同样被判漂移。
        Index(
            'uq_memories_user_hash_bucket',
            'user_id', 'content_hash', 'dedup_bucket',
            unique=True,
            postgresql_where=text("content_hash IS NOT NULL AND dedup_bucket IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    content: Mapped[str] = mapped_column(Text, nullable=False, comment='记忆原始内容')
    importance: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('3'), comment='重要程度1-5')
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    user_id: Mapped[str | None] = mapped_column(String, comment='用户ID（用于用户数据隔离）')
    summary: Mapped[str | None] = mapped_column(Text, comment='AI生成的摘要')
    category_id: Mapped[int | None] = mapped_column(Integer, comment='分类ID')
    tags: Mapped[dict | None] = mapped_column(JSON, comment='标签列表（旧字段，已废弃）')
    mood: Mapped[str | None] = mapped_column(String(20), comment='情绪状态')
    location: Mapped[str | None] = mapped_column(String(200), comment='地点')
    people: Mapped[dict | None] = mapped_column(JSON, comment='相关人物列表')
    content_format: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'text'"), comment='内容形式：text/image/voice/link'
    )
    topics: Mapped[dict | None] = mapped_column(JSON, comment='L3主题领域列表')
    updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    # ── 判重幂等键（2026-10-06，迁移 f2c3d4e5a6b7）──
    # 与 `dedup_bucket` 组成**部分唯一索引**，把"判重与写入分属两个事务"的
    # TOCTOU 关掉。哈希口径与 `memory_tools.DEDUP_PREFIX_LEN` 一致（归一化后取前 50 字）
    # —— 因为 LLM 重试是把内容**变长**（尾部追加时间/语气词），全内容哈希抓不住。
    content_hash: Mapped[str | None] = mapped_column(String(64), comment='归一化内容前50字的sha256（判重幂等键）')
    dedup_bucket: Mapped[int | None] = mapped_column(Integer, comment='判重时间桶=epoch//(窗口分钟×60)')

    category: Mapped[Optional['MemoryCategories']] = relationship('MemoryCategories', back_populates='memories')


class KnowledgeCollections(Base):
    """知识收藏库表 - 存储用户收藏的外部信息"""
    __tablename__ = 'knowledge_collections'
    __table_args__ = (
        PrimaryKeyConstraint('id', name='knowledge_collections_pkey'),
        Index('ix_knowledge_user_id', 'user_id'),
        Index('ix_knowledge_content_type', 'content_type'),
        Index('ix_knowledge_created_at', 'created_at'),
        Index('ix_knowledge_importance', 'importance'),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String, comment='用户ID')
    content: Mapped[str] = mapped_column(Text, nullable=False, comment='内容')
    content_type: Mapped[str] = mapped_column(
        String(50), nullable=False, comment='内容类型：article/post/opinion/data/visual/other'
    )
    summary: Mapped[str | None] = mapped_column(Text, comment='AI生成的摘要')
    title: Mapped[str | None] = mapped_column(String(500), comment='标题')
    author: Mapped[str | None] = mapped_column(String(200), comment='作者')
    source: Mapped[str | None] = mapped_column(String(500), comment='来源（URL/平台/出处）')
    source_date: Mapped[datetime.datetime | None] = mapped_column(DateTime(True), comment='来源发布日期')
    keywords: Mapped[dict | None] = mapped_column(JSON, comment='关键词列表')
    topics: Mapped[dict | None] = mapped_column(JSON, comment='主题分类')
    image_url: Mapped[str | None] = mapped_column(String(500), comment='图片URL（视觉信息）')
    related_memory_ids: Mapped[dict | None] = mapped_column(JSON, comment='关联的主观记忆ID列表')
    importance: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text('3'), comment='重要程度1-5')
    created_at: Mapped[datetime.datetime] = mapped_column(DateTime(True), nullable=False, server_default=text('now()'))
    content_format: Mapped[str | None] = mapped_column(
        String(20), server_default=text("'text'"), comment='内容形式：text/image/voice/link'
    )
    updated_at: Mapped[datetime.datetime | None] = mapped_column(DateTime(True))
    # L2 情绪（八选一，**收藏库可选**）—— 2026-10-06 迁移 e1b2c3d4f5a6 补列。
    # 此前只有 memories 有 mood，而标签体系 SOP 与 `save_knowledge_collection`
    # 都支持收藏带情绪 ⇒ 带情绪的收藏必然写入失败（列不存在）。补列的更多说明见迁移文件。
    mood: Mapped[str | None] = mapped_column(String(20), comment='L2 情绪状态（八选一，收藏库可选）')
