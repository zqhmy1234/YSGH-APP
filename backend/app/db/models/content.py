"""内容域 + 纠错域 ORM 模型（contents/correction_log，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, Index, String, Text, UniqueConstraint, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models._base import _uuid
from app.db.session import Base


class Content(Base):
    """contents 核心表（照片/文字/语音/文章统一入库，API-002）

    去重（Q16）：同用户 perceptual_hash 唯一（PG 多 NULL 不冲突，
    text/voice 无哈希不受约束）；修复：ORM 补唯一约束防并发双写（审查 MAJOR）。

    R4#4（创建端点幂等键）：client_generated_id 为客户端生成的幂等键，
    (user_id, client_generated_id) 部分唯一索引（PG 多 NULL 不冲突 → 仅非空参与），
    photo/voice 既有幂等（perceptual_hash 409 / cos_key）保留为兜底。
    """

    __tablename__ = "contents"
    __table_args__ = (
        UniqueConstraint("user_id", "perceptual_hash", name="uq_contents_user_hash"),
        Index(
            "uq_contents_user_client_generated_id",
            "user_id",
            "client_generated_id",
            unique=True,
            postgresql_where=sa_text("client_generated_id IS NOT NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    content_type: Mapped[str] = mapped_column(String)
    content_class: Mapped[str | None] = mapped_column(String, nullable=True)
    class_source: Mapped[str | None] = mapped_column(String, nullable=True)
    model_version: Mapped[str | None] = mapped_column(String, nullable=True)
    text: Mapped[str | None] = mapped_column(Text, nullable=True)
    taken_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    gps_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    gps_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    place: Mapped[str | None] = mapped_column(String, nullable=True)
    perceptual_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    client_generated_id: Mapped[str | None] = mapped_column(
        String(64), nullable=True, index=False
    )  # R4#4 幂等键（客户端生成，同用户唯一）
    emotion: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sensitive_tags: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    sensitive_status: Mapped[str] = mapped_column(String, default="正常")
    qdrant_text_id: Mapped[str | None] = mapped_column(String, nullable=True)
    qdrant_image_id: Mapped[str | None] = mapped_column(String, nullable=True)
    cos_key: Mapped[str | None] = mapped_column(String, nullable=True)
    thumbnail_key: Mapped[str | None] = mapped_column(String, nullable=True)
    source: Mapped[str] = mapped_column(String, default="app")
    extra: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String, default="processing")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class CorrectionLog(Base):
    """correction_log 表（B5-c 三层裁决第①层：个人规则数据源）

    对齐 schema.sql 纠错域：
    - content_embedding 按 schema 注记改为 qdrant_point_id（MVP 向量检索走 Qdrant，零新增）
    - 同内容多次纠错以最后一次为准（查询时按 content_id 取最新）
    - 保留最近 500 条/用户（超出由 service 层裁剪）
    - is_global_candidate：共性纠错标记（多用户一致 → 全局微调候选，≥50 触发）
    """

    __tablename__ = "correction_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    content_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    content_type: Mapped[str] = mapped_column(String, default="text")
    qdrant_point_id: Mapped[str | None] = mapped_column(String, nullable=True)
    old_label: Mapped[str | None] = mapped_column(String, nullable=True)
    new_label: Mapped[str] = mapped_column(String)
    source: Mapped[str] = mapped_column(String, default="active")  # active / echo / org
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    is_global_candidate: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
