import logging
import re
import time
from typing import Optional, Union

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

# 数据库连接超时时间（秒），每次尝试 15 秒，共尝试 2 次
DB_CONNECTION_TIMEOUT = 15
DB_MAX_RETRIES = 2

# SQLAlchemy 风格的驱动后缀（`postgresql+psycopg://`）**原生 psycopg 不认**：
# psycopg 的 conninfo 解析器把 `+psycopg` 当成非法 token，直接报
# `ProgrammingError: missing "=" after "postgresql+psycopg://..." in connection info string`。
# 而 `DATABASE_URL` 只有一个来源（`storage/database/db.py:31`，原样交给 SQLAlchemy
# `create_engine`），那个形态对 SQLAlchemy 是**必需的** ⇒ 不能改 .env 迁就原生 psycopg，
# 只能在「喂给原生 psycopg 之前」做一次规范化。这也正是本函数存在的唯一理由：
# 同一份配置喂两种客户端，各要各的形态。
_DRIVER_SUFFIX_RE = re.compile(r"^([a-zA-Z0-9+]+)\+[a-zA-Z0-9_]+://")


def as_native_psycopg_url(url: str) -> str:
    """把 SQLAlchemy 风格的驱动后缀剥掉，得到原生 psycopg 能解析的 conninfo。

    `postgresql+psycopg://u:p@h:5432/db` → `postgresql://u:p@h:5432/db`
    非 SQLAlchemy 形态（无 `+` 后缀）原样返回，因此**幂等且对存量配置零影响**。
    """
    return _DRIVER_SUFFIX_RE.sub(r"\1://", url.strip())


class MemoryManager:
    """Memory Manager 单例类"""

    _instance: Optional['MemoryManager'] = None
    _checkpointer: Optional[Union[PostgresSaver, MemorySaver]] = None
    _pool: Optional[AsyncConnectionPool] = None
    _conn: Optional[psycopg.Connection] = None
    _setup_done: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _connect_with_retry(self, db_url: str) -> Optional[psycopg.Connection]:
        """带重试的数据库连接，每次 15 秒超时，共尝试 2 次"""
        last_error = None
        for attempt in range(1, DB_MAX_RETRIES + 1):
            try:
                logger.info(f"Attempting database connection (attempt {attempt}/{DB_MAX_RETRIES})")
                conn = psycopg.connect(db_url, autocommit=True, connect_timeout=DB_CONNECTION_TIMEOUT)
                logger.info(f"Database connection established on attempt {attempt}")
                return conn
            except Exception as e:
                last_error = e
                logger.warning(f"Database connection attempt {attempt} failed: {e}")
                if attempt < DB_MAX_RETRIES:
                    time.sleep(1)  # 重试前短暂等待
        logger.error(f"All {DB_MAX_RETRIES} database connection attempts failed, last error: {last_error}")
        return None

    def _setup_schema_and_tables(self, db_url: str) -> bool:
        """同步创建 schema 和表（只执行一次），返回是否成功"""
        if self._setup_done:
            return True

        conn = self._connect_with_retry(db_url)
        if conn is None:
            return False

        try:
            with conn.cursor() as cur:
                cur.execute("CREATE SCHEMA IF NOT EXISTS memory")
            conn.execute("SET search_path TO memory")
            PostgresSaver(conn).setup()
            self._setup_done = True
            logger.info("Memory schema and tables created")
            return True
        except Exception as e:
            logger.warning(f"Failed to setup schema/tables: {e}")
            return False
        finally:
            conn.close()

    def _get_db_url_safe(self) -> Optional[str]:
        """安全获取 db_url，失败时返回 None

        规范化在此收口：`get_db_url()` 返回的是 **SQLAlchemy 形态**（给 `db.py` 的
        `create_engine` 用），而本类下游是**原生 psycopg**，必须先剥驱动后缀。
        """
        try:
            from storage.database.db import get_db_url
            db_url = get_db_url()
            if db_url and db_url.strip():
                return as_native_psycopg_url(db_url)
            logger.warning("db_url is empty, will fallback to MemorySaver")
            return None
        except Exception as e:
            logger.warning(f"Failed to get db_url: {e}, will fallback to MemorySaver")
            return None

    def _create_fallback_checkpointer(self) -> MemorySaver:
        """内存兜底 checkpointer。

        **刻意不写入 `self._checkpointer`**：降级只对本次调用生效，下次仍会重试建库
        （原实现把兜底结果缓存进单例字段，导致「启动时DB 抖一下 → 进程整个生命周期
        永久失忆且不可恢复」）。返回的实例由调用方使用，不进缓存。
        """
        logger.warning(
            "Using MemorySaver as fallback checkpointer "
            "(对话上下文本次可用，但重启后不保留——多轮上下文实际未持久化)"
        )
        return MemorySaver()

    def get_checkpointer(self) -> BaseCheckpointSaver:
        """获取 checkpointer，优先 PostgresSaver（上下文真落库），失败退化为 MemorySaver。

        **为什么用同步 `PostgresSaver` 而不是 `AsyncPostgresSaver`**（2026-10-04 实测修复）：
        上游原实现用 `AsyncConnectionPool` + `AsyncPostgresSaver`，但本服务的调用链
        `main.py:/v1/chat`（**同步 def**）→ `run_agent_with_validation` → `agent.invoke()`
        **全程同步、且运行在 threadpool 里没有 running loop** ⇒ `AsyncConnectionPool.open()`
        抛 `AsyncConnectionPool open with no running loop` ⇒ 静默降级 MemorySaver。
        后果不是报错而是**更坏的形态**：对话能正常往返，但每轮都是新会话（用户以为有记忆，
        实际每次失忆），且仅 WARNING 日志、用户侧零感知。
        同步 `PostgresSaver` 与 `agent.invoke()` 天然匹配，且不需要事件循环。
        """
        if self._checkpointer is not None:
            return self._checkpointer

        # 1. 尝试获取 db_url（已规范化为原生 psycopg 可解析的形态）
        db_url = self._get_db_url_safe()
        if not db_url:
            return self._create_fallback_checkpointer()

        # 2. 尝试连接数据库并创建 schema/表（带重试）
        if not self._setup_schema_and_tables(db_url):
            return self._create_fallback_checkpointer()

        # 3. 建同步 saver：**连接必须长驻**（saver 持有它做每次 checkpoint 的读写），
        #    所以不能再走 `_setup_schema_and_tables` 里那个 finally close 的一次性连接。
        #    search_path 是**连接级**设置，必须在这条长驻连接上重新设一次。
        try:
            conn = self._connect_with_retry(db_url)
            if conn is None:
                logger.warning("PostgresSaver: 长驻连接获取失败")
                return self._create_fallback_checkpointer()
            with conn.cursor() as cur:
                cur.execute("SET search_path TO memory")
            self._conn = conn
            self._checkpointer = PostgresSaver(conn)
            logger.info("PostgresSaver initialized successfully (对话上下文将持久化到 Postgres)")
        except Exception as e:
            logger.warning(f"Failed to create PostgresSaver: {e}, will fallback to MemorySaver")
            return self._create_fallback_checkpointer()

        return self._checkpointer

    def checkpointer_kind(self) -> str:
        """当前 checkpointer 的真实类型（供 /health 暴露，避免降级对用户不可见）。"""
        if self._checkpointer is None:
            return "未初始化"
        if isinstance(self._checkpointer, PostgresSaver):
            return "PostgresSaver（持久化）"
        if isinstance(self._checkpointer, MemorySaver):
            return "MemorySaver（降级·不持久）"
        return type(self._checkpointer).__name__

_memory_manager: Optional[MemoryManager] = None


def get_memory_saver() -> BaseCheckpointSaver:
    """获取 checkpointer，优先使用 PostgresSaver，db_url 不可用或连接失败时退化为 MemorySaver"""
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager.get_checkpointer()


def current_checkpointer_kind() -> str:
    """当前 checkpointer 的真实类型（**只读**，不触发建连/建表）。

    供服务 `/health` 暴露用：降级到 MemorySaver 时对话照常往返、一轮轮都是新会话，
    光看"服务 200 + 有回复"发现不了，只有把类型报出来才可见。
    """
    if _memory_manager is None or _memory_manager._checkpointer is None:
        return "未初始化"
    return _memory_manager.checkpointer_kind()