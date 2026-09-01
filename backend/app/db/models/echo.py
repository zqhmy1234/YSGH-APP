"""回响域 ORM 模型（echo_history，对齐 backend/sql/schema.sql）"""
from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, Index, String, func
from sqlalchemy import text as sa_text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class EchoHistory(Base):
    """echo_history 表（B5-a：回响每天 ≤1 条 / 划掉不再出现）

    修复（审查 MAJOR 竞态）：UNIQUE(user_id, event_id) 因 event_id 恒为 NULL 失效
    （PG 多 NULL 不冲突）。补部分唯一索引 (user_id, shown_date) WHERE action
    <> 'dismiss'——DB 层兜底"每天 ≤1 条"，并发双请求只有一条能插入。
    shown_date：展示日（本地日界，应用写入；timestamptz::date 非 IMMUTABLE 不能建索引，
    故显式落列，日界口径单一来源 = 应用侧 _local_now()）。
    """

    __tablename__ = "echo_history"
    __table_args__ = (
        Index(
            "uq_echo_history_daily",
            "user_id",
            "shown_date",
            unique=True,
            postgresql_where=sa_text("action IS DISTINCT FROM 'dismiss'"),
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=False), index=True)
    event_id: Mapped[str | None] = mapped_column(UUID(as_uuid=False), nullable=True)
    shown_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    shown_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    action: Mapped[str | None] = mapped_column(String, nullable=True)  # respond / dismiss / suppressed
    fingerprint: Mapped[str | None] = mapped_column(String, nullable=True)
