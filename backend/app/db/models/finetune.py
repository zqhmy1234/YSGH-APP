"""共性纠错微调任务；backend/scripts/reflow_global.py 使用原生 SQL 读写。"""

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class FinetuneJob(Base):
    __tablename__ = "finetune_jobs"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trigger: Mapped[str] = mapped_column(String, nullable=False)
    dataset_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, default="pending", server_default=text("'pending'"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
