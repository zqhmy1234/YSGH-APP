"""认证域 ORM 模型（users/devices/sms_codes，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.models._base import _uuid
from app.db.session import Base


class User(Base):
    """users 表（决策 #8：unionid 主键 + 手机号备用）"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    unionid: Mapped[str | None] = mapped_column(String, unique=True)
    phone: Mapped[str | None] = mapped_column(String, unique=True)
    nickname: Mapped[str | None] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String)
    status: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class Device(Base):
    """devices 表（refresh_token 可吊销，AUTH-006）

    修复（审查 MAJOR 遗漏）：补 UNIQUE(user_id, device_id)（schema.sql 已有，ORM 漂移）
    """

    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_devices_user_device"),)

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    platform: Mapped[str] = mapped_column(String)
    # TD-P3 M6（审查中危/低危）：refresh_token 不再明文落库——只存哈希 + 最后轮换时间。
    # refresh_token 明文列保留用于迁移期兼容（存量行哈希化为空时回退比对；登录即覆写清空）。
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_token_hash: Mapped[str | None] = mapped_column(String, nullable=True)
    refresh_rotated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SmsCode(Base):
    """sms_codes 表（防刷：限流+有效期，AUTH-003/004）

    G1/R6#9：code 只存 SHA-256+盐 哈希（salt 随行落库，校验按行盐重算）——
    存量无盐记录（salt 为空）走无盐 SHA-256 比对兼容。
    """

    __tablename__ = "sms_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String, index=True)
    code: Mapped[str] = mapped_column(String)
    salt: Mapped[str | None] = mapped_column(String, nullable=True)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
