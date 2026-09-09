"""数据库会话（SQLAlchemy 2.0 + psycopg3）"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,          # 连接失效自动重连（测试清单 API-012）
    # P0-4（2026-09-10 重构审计）：压测报告定位 DB 池为 100 并发瓶颈——
    # 5+10 在「api 多 worker + RQ worker 共库」下秒穿；扩至 10+20（合计 30，
    # PG 默认 max_connections=100 留余量给测试直连/迁移脚本），
    # recycle=30min 防闲置连接被网络层静默掐断（pre_ping 之外的双保险）。
    pool_size=10,
    max_overflow=20,
    pool_recycle=1800,
    pool_timeout=30,             # 池满显式等待上限（防请求无限堆积放大故障）
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    """ORM 基类"""


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
