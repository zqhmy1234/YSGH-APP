"""上传域 ORM 模型（upload_tasks/upload_chunks，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models._base import _uuid
from app.db.session import Base


class UploadTask(Base):
    """upload_tasks 表（S5-03 COS 分片上传/断电续传，WP-C 2026-08-19）

    client_upload_id 为客户端幂等键（同一文件重传复用任务），
    file_key 为最终对象键；分片状态在 upload_chunks。
    """

    __tablename__ = "upload_tasks"
    __table_args__ = (
        UniqueConstraint("user_id", "client_upload_id", name="uq_upload_tasks_user_client"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    client_upload_id: Mapped[str] = mapped_column(String)
    file_name: Mapped[str] = mapped_column(String)
    file_size: Mapped[int] = mapped_column(BigInteger)
    chunk_size: Mapped[int] = mapped_column(BigInteger)
    chunk_count: Mapped[int] = mapped_column(BigInteger)
    file_key: Mapped[str] = mapped_column(String)
    storage: Mapped[str] = mapped_column(String, default="fake")
    status: Mapped[str] = mapped_column(String, default="pending")  # pending/uploading/completed/failed
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class UploadChunk(Base):
    """upload_chunks 表（分片状态：断电续传依据）"""

    __tablename__ = "upload_chunks"
    __table_args__ = (
        UniqueConstraint("upload_id", "chunk_index", name="uq_upload_chunks_task_index"),
    )

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    upload_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    chunk_index: Mapped[int] = mapped_column(BigInteger)
    chunk_hash: Mapped[str] = mapped_column(String)
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String, default="uploaded")  # uploaded 即已落存储
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
