"""画像域 + 敏感词域 ORM 模型（B1/B5b，对齐 backend/sql/schema.sql）"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, String, Text, UniqueConstraint, func, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class UserProfile(Base):
    """user_profile 表（B1：稀疏高维枚举画像，冷启动三问激活 L0/L1）"""

    __tablename__ = "user_profile"

    # D-13：FK 声明与库对齐（库中已有 user_profile_user_id_fkey，仅补 ORM 声明）
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", name="user_profile_user_id_fkey"), primary_key=True
    )
    version: Mapped[int] = mapped_column(default=1, server_default=text("1"))
    dimensions: Mapped[dict] = mapped_column(JSONB, default=dict, server_default=text("'{}'"))
    token_usage: Mapped[int] = mapped_column(BigInteger, default=0, server_default=text("0"))
    last_rebuilt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileDimensionHistory(Base):
    """profile_dimension_history 表（B1：历史值保留最近 10 条）"""

    __tablename__ = "profile_dimension_history"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # D-13：FK 声明与库对齐（库中已有 profile_dimension_history_user_id_fkey，仅补 ORM 声明）
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="profile_dimension_history_user_id_fkey"),
        index=True,
    )
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
    __table_args__ = (UniqueConstraint("user_id", "dimension", "raw_answer", name="uq_pdp_user_dim_raw"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # D-13：FK 声明与库对齐（库中已有 profile_dimension_pending_user_id_fkey，仅补 ORM 声明）
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False),
        ForeignKey("users.id", name="profile_dimension_pending_user_id_fkey"),
        index=True,
    )
    dimension: Mapped[str] = mapped_column(String)  # relation_core / life_events / values_priority
    raw_answer: Mapped[str] = mapped_column(String)  # 未命中枚举的原始回答
    count: Mapped[int] = mapped_column(BigInteger, default=1, server_default=text("1"))  # 同类累计
    status: Mapped[str] = mapped_column(
        String, default="pending", server_default=text("'pending'")
    )  # pending / confirmed / rejected
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
        UniqueConstraint("user_id", "topic_hash", name="profile_sensitive_user_id_topic_hash_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # D-13：FK 声明与库对齐（库中已有 profile_sensitive_user_id_fkey，仅补 ORM 声明）
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", name="profile_sensitive_user_id_fkey"), index=True
    )
    # Retained for one rollout as rollback data. Never written by new code;
    # remove with a separate migration after the encrypted path is verified.
    legacy_topic: Mapped[str | None] = mapped_column("topic", String, nullable=True)
    topic_enc: Mapped[str] = mapped_column(Text, nullable=False)
    topic_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    @property
    def topic(self) -> str:
        from app.services.profile_sensitive_crypto import decrypt_topic

        return decrypt_topic(self.topic_enc)

    @topic.setter
    def topic(self, value: str) -> None:
        from app.services.profile_sensitive_crypto import encrypt_topic

        self.topic_enc, self.topic_hash = encrypt_topic(value)
    disposition: Mapped[str] = mapped_column(
        String, default="forbid", server_default=text("'forbid'")
    )  # allow/mention/caution/review/forbid
    evidence: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"))
    locked: Mapped[bool] = mapped_column(default=False, server_default=text("false"))  # 用户显式标记
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class ProfileL2Evidence(Base):
    """画像内容级证据；标注与清除服务使用原生 SQL 读写。"""

    __tablename__ = "profile_l2_evidence"
    __table_args__ = (Index("idx_l2_evidence_user_dim", "user_id", "dimension"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    dimension: Mapped[str] = mapped_column(String, nullable=False)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    evidence_content_ids: Mapped[list] = mapped_column(JSONB, default=list, server_default=text("'[]'"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class SensitiveWord(Base):
    """sensitive_words 表（B5b 三层敏感词表：预置基础/画像标记驱动/违规词回流）

    Wave 0 重建（迁移 b0b1c2d3e4f5），供事件级敏感分类器与违规词回流使用。
    """

    __tablename__ = "sensitive_words"
    __table_args__ = (UniqueConstraint("word", "user_id", name="sensitive_words_word_user_id_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    word: Mapped[str] = mapped_column(String)
    level: Mapped[int] = mapped_column(default=1)  # 1=预置 2=画像标记驱动 3=违规词回流
    # D-13：FK 声明与库对齐（库中已有 sensitive_words_user_id_fkey，仅补 ORM 声明）
    user_id: Mapped[str | None] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", name="sensitive_words_user_id_fkey"), nullable=True
    )  # NULL=全局
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ProfileAnnotationPool(Base):
    """profile_annotation_pool 表（B1 低置信度事件池，设计 2.3）

    置信度 <0.7 的标注候选不进画像，入本池周级批量复核；
    Wave 0 新增（迁移 b0b1c2d3e4f5）。
    """

    __tablename__ = "profile_annotation_pool"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # D-13：FK 声明与库对齐（库中已有 profile_annotation_pool_user_id_fkey，仅补 ORM 声明）
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", name="profile_annotation_pool_user_id_fkey"), index=True
    )
    event_id: Mapped[str | None] = mapped_column(String, nullable=True)
    raw_text: Mapped[str] = mapped_column(String)
    dimension: Mapped[str | None] = mapped_column(String, nullable=True)
    candidate_value: Mapped[str | None] = mapped_column(String, nullable=True)
    confidence: Mapped[float] = mapped_column(default=0.0, server_default=text("0"))
    status: Mapped[str] = mapped_column(
        String, default="pending", server_default=text("'pending'")
    )  # pending/reviewed/confirmed/discarded
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
