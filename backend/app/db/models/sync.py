"""同步域 ORM 模型（sync_state/offline_queue/deleted_logs/sync_field_versions，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class SyncState(Base):
    """sync_state 表（B4-2：每端同步游标，增量拉取幂等）"""

    __tablename__ = "sync_state"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    cursor_version: Mapped[int] = mapped_column(BigInteger, default=0)
    last_sync_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OfflineQueue(Base):
    """offline_queue 表（B4-3：云端幂等去重 + 变更日志，op_id 按用户唯一）

    双角色：
    1. 客户端操作幂等（(user_id, op_id) 唯一：网络重试同一操作只执行一次）
    2. 增量拉取源（id 全局单调 = 同步游标）

    安全修复：op_id 唯一约束从全局改为 (user_id, op_id) 复合——
    防跨用户 op_id 碰撞导致他人操作被幂等跳过（审查 CRITICAL）。
    """

    __tablename__ = "offline_queue"
    __table_args__ = (UniqueConstraint("user_id", "op_id", name="uq_offline_queue_user_op"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    op_id: Mapped[str] = mapped_column(String)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    op_type: Mapped[str] = mapped_column(String)
    payload: Mapped[dict] = mapped_column(JSONB)
    status: Mapped[str] = mapped_column(String, default="done")
    retry_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class DeletedLog(Base):
    """deleted_logs 表（B4-2：软删除 30 天物理清理对账）"""

    __tablename__ = "deleted_logs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    content_id: Mapped[str] = mapped_column(UUID(as_uuid=False))
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    deleted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    cleanup_status: Mapped[str] = mapped_column(String, default="pending")


class SyncFieldVersion(Base):
    """sync_field_versions 表（B4-2：字段级 LWW 版本存储，云端权威）

    每 (entity_type, entity_id, field) 一行：value + updated_at + user_id（归属）；
    LWW 比较：客户端 updated_at > 云端 → 更新；否则云端胜。
    deleted 标记 entity 级软删除墓碑（同步到各端）。
    user_id：实体归属（越权校验：push 时非本人实体拒绝，B4 安全修复）。
    """

    __tablename__ = "sync_field_versions"

    entity_type: Mapped[str] = mapped_column(String, primary_key=True)
    entity_id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    field: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    value: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted: Mapped[bool] = mapped_column(default=False)
