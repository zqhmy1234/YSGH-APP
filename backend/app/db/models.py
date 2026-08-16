"""ORM 模型（对齐 backend/sql/schema.sql）

MVP 第一期按需逐表补充；当前覆盖认证域（users/devices/sms_codes），
支撑 S1-02 认证真实接入 DB（AUTH-001/003/005/006）。
"""
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class User(Base):
    """users 表（决策 #8：unionid 主键 + 手机号备用）"""

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(UUID(as_uuid=False), primary_key=True)
    unionid: Mapped[str | None] = mapped_column(String, unique=True)
    phone: Mapped[str | None] = mapped_column(String, unique=True)
    nickname: Mapped[str | None] = mapped_column(String)
    avatar: Mapped[str | None] = mapped_column(String)
    status: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    deleted_by: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)


class Device(Base):
    """devices 表（refresh_token 可吊销，AUTH-006）"""

    __tablename__ = "devices"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    device_id: Mapped[str] = mapped_column(String)
    platform: Mapped[str] = mapped_column(String)
    refresh_token: Mapped[str | None] = mapped_column(String, nullable=True)
    last_active_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SmsCode(Base):
    """sms_codes 表（防刷：限流+有效期，AUTH-003/004）"""

    __tablename__ = "sms_codes"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    phone: Mapped[str] = mapped_column(String, index=True)
    code: Mapped[str] = mapped_column(String)
    expire_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
