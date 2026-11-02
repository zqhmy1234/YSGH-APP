import logging
import os
import time

from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

MAX_RETRY_TIME = 20  # 连接最大重试时间（秒）
# 环境变量：读 agent/.env（**绝对路径**，不依赖启动时的 CWD——服务可能从任意目录拉起）
try:
    from pathlib import Path

    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[3] / ".env", override=False)
except Exception:
    pass


def get_db_url() -> str:
    """数据库连接串。

    去 Coze（2026-09-23 · AG4）：上游在 `PGDATABASE_URL` 缺失时**从 Coze workload identity
    拉环境变量**（`coze_workload_identity.Client`）。本服务只认本地环境：优先 `DATABASE_URL`
    （`agent/.env` 口径，与 backend 同库同账号），兼容上游的 `PGDATABASE_URL`；
    两者都缺 → **显式抛错**（不静默、不降级——缺配置静默跑起来只会产出假数据）。
    """
    url = (os.getenv("DATABASE_URL") or os.getenv("PGDATABASE_URL") or "").strip()
    if not url:
        raise RuntimeError(
            "未配置 DATABASE_URL（agent/.env）—— 记忆三表在我们自己的 Postgres，连接串必填"
        )
    return url
_engine = None
_SessionLocal = None

def _create_engine_with_retry():
    url = get_db_url()
    if url is None or url == "":
        logger.error("PGDATABASE_URL is not set")
        raise ValueError("PGDATABASE_URL is not set")
    size = 100
    overflow = 100
    recycle = 1800
    timeout = 30
    engine = create_engine(
        url,
        pool_size=size,
        max_overflow=overflow,
        pool_pre_ping=True,
        pool_recycle=recycle,
        pool_timeout=timeout,
    )
    # 验证连接，带重试
    start_time = time.time()
    last_error = None
    while time.time() - start_time < MAX_RETRY_TIME:
        try:
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return engine
        except OperationalError as e:
            last_error = e
            elapsed = time.time() - start_time
            logger.warning(f"Database connection failed, retrying... (elapsed: {elapsed:.1f}s)")
            time.sleep(min(1, MAX_RETRY_TIME - elapsed))
    logger.error(f"Database connection failed after {MAX_RETRY_TIME}s: {last_error}")
    raise last_error  # pyright: ignore [reportGeneralTypeIssues]

def get_engine():
    global _engine
    if _engine is None:
        _engine = _create_engine_with_retry()
    return _engine

def get_sessionmaker():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=get_engine())
    return _SessionLocal

def get_session():
    return get_sessionmaker()()

__all__ = [
    "get_db_url",
    "get_engine",
    "get_sessionmaker",
    "get_session",
]
