"""画像域 + 敏感词域 ORM 模型（B1/B5b，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserProfile(Base):
    """user_profile 表（B1：稀疏高维枚举画像，冷启动三问激活 L0/L1）"""

    __tablename__ = "user_profile"

    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    version: Mapped[int] = mapped_column(default=1)
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict)
    token_usage: Mapped[int] = mapped_column(BigInteger, default=0)
    last_rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileDimensionHistory(Base):
    """profile_dimension_history 表（B1：历史值保留最近 10 条）"""

    __tablename__ = "profile_dimension_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    dimension: Mapped[str] = mapped_column(String)
    value: Mapped[str] = mapped_column(String)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileDimensionPending(Base):
    """profile_dimension_pending 表（B1 维度扩展队列）

    枚举集无合适值 → 不自动加（标注是映射不是生成），原始回答进本队列，
    累计同类后人工确认再扩枚举（B1 2.3：累计 N 次同类 → 人工确认后加值）。
    """

    __tablename__ = "profile_dimension_pending"
    __table_args__ = (
        UniqueConstraint("user_id", "dimension", "raw_answer", name="uq_pdp_user_dim_raw"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    dimension: Mapped[str] = mapped_column(String)  # relation_core / life_events / values_priority
    raw_answer: Mapped[str] = mapped_column(String)  # 未命中枚举的原始回答
    count: Mapped[int] = mapped_column(BigInteger, default=1)  # 同类累计
    status: Mapped[str] = mapped_column(String, default="pending")  # pending / confirmed / rejected
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileSensitive(Base):
    """profile_sensitive 表（B1 v1.1 修订 + B5b §4：画像级敏感，永不过期）

    话题×处置 5 级（allow/mention/caution/review/forbid）+ 证据 + 生命周期；
    红线级（涉政/违法/未成年/医疗诊断）不进画像，走 B5-b 护栏硬规则。
    Wave 0 重建（迁移 b0b1c2d3e4f5），供 B5b 回响双查 L1 校验接线。
    """

    __tablename__ = "profile_sensitive"
    __table_args__ = (
        UniqueConstraint("user_id", "topic", name="profile_sensitive_user_id_topic_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    topic: Mapped[str] = mapped_column(String)
    topic_hash: Mapped[str | None] = mapped_column(String, nullable=True)  # HMAC 盲索引预留
    disposition: Mapped[str] = mapped_column(String, default="forbid")  # allow/mention/caution/review/forbid
    evidence: Mapped[list] = mapped_column(JSONB, default=list)
    locked: Mapped[bool] = mapped_column(default=False)  # 用户显式标记
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class SensitiveWord(Base):
    """sensitive_words 表（B5b 三层敏感词表：预置基础/画像标记驱动/违规词回流）

    Wave 0 重建（迁移 b0b1c2d3e4f5），供事件级敏感分类器与违规词回流使用。
    """

    __tablename__ = "sensitive_words"
    __table_args__ = (
        UniqueConstraint("word", "user_id", name="sensitive_words_word_user_id_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String)
    level: Mapped[int] = mapped_column(default=1)  # 1=预置 2=画像标记驱动 3=违规词回流
    user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)  # NULL=全局
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileAnnotationPool(Base):
    """profile_annotation_pool 表（B1 低置信度事件池，设计 2.3）

    置信度 <0.7 的标注候选不进画像，入本池周级批量复核；
    Wave 0 新增（迁移 b0b1c2d3e4f5）。
    """

    __tablename__ = "profile_annotation_pool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_text: Mapped[str] = mapped_column(String)
    dimension: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_value: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.0)
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/reviewed/confirmed/discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
