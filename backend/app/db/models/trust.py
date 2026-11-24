"""B6 信任域 ORM 模型（2026-11-24）。

三张表各管一件事，互不混用：
  consent_grant —— 用途授权：数据能干什么用，用户勾选才放行，撤回不倒流
  vault_entry   —— 保险箱：内容级锁定的覆盖层（不改 contents 原行）
  audit_log     —— 安全审计：授权变更/解锁/导出/注销等关键操作留痕，用户可见

设计约束（用户拍板 2026-11-24）：
  - 用途按「用途」列 4-5 项，不做数据类别×用途二维矩阵
  - 关闭授权不倒流（已生成内容保留），只管后续使用
  - 保险箱一条条锁；谁能解锁 = 本人 + 被授权家庭成员（后者依赖 B8，本期留接口）
  - 审计记关键动作、用户可查自己的记录
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class ConsentGrant(Base):
    """用途授权：一行 = 一个用户对一个用途的当前状态。

    scope 走白名单（服务层校验），不在此处做 CHECK——新增用途只改常量。
    state: granted / revoked；无行 = 未表态（视为未授权，默认不勾）。
    撤回保留行（state='revoked'）以便审计，不物理删除。
    """

    __tablename__ = "consent_grant"
    __table_args__ = (
        UniqueConstraint("user_id", "scope", name="uq_consent_grant_user_scope"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    scope: Mapped[str] = mapped_column(String(48))
    state: Mapped[str] = mapped_column(String(16), default="granted", server_default=text("'granted'"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class VaultEntry(Base):
    """保险箱条目：覆盖层，锁的是 contents 里的一条内容，不改原行。

    解锁语义：本人持服务端钥匙即可开；家庭成员解锁依赖 B8（本期仅留 granted_roles 字段）。
    不存原文密文——原文本体仍由 contents 管理，锁的是访问入口（检索/回响/导出/共享四处过滤）。
    """

    __tablename__ = "vault_entry"
    __table_args__ = (
        Index("ix_vault_entry_user_created", "user_id", "created_at"),
        # 只约束「当前锁定」行：解锁标记 unlocked_at 后可再锁，新行 id 即新令牌轮次。
        Index(
            "uq_vault_entry_active", "user_id", "content_id", unique=True,
            postgresql_where=text("unlocked_at IS NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    content_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    # 预留 B8：JSON 数组，记录被授权的家庭角色；当前恒空（本人端无家庭实体）
    granted_roles: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    unlocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    """安全审计：关键动作的不可篡改留痕（只增不改不删，账号注销时随用户清除）。

    actor=user_id（本人操作）；target 是被操作的对象 id 或 scope 名，不存敏感原文。
    """

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_user_created", "user_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    action: Mapped[str] = mapped_column(String(48), index=True)
    target: Mapped[str | None] = mapped_column(String(128), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


__all__ = ["ConsentGrant", "VaultEntry", "AuditLog"]
