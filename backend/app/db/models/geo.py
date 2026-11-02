"""地理缓存域 ORM 模型（geo_cache，对齐 backend/sql/schema.sql）"""
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.session import Base


class GeoCache(Base):
    """geo_cache 表（高德逆地理缓存 · 外部API清单 #5）

    geohash 精度 6（≈1.2km 格子）作缓存键，同格复用一次逆编码调用；
    高德合规：逆地理结果不可缓存超 30 天（service 层读取时校验 updated_at 年龄）。
    """

    __tablename__ = "geo_cache"

    geohash: Mapped[str] = mapped_column(String, primary_key=True)  # 精度 6
    place: Mapped[str | None] = mapped_column(String, nullable=True)
    city: Mapped[str | None] = mapped_column(String, nullable=True)
    province: Mapped[str | None] = mapped_column(String, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
