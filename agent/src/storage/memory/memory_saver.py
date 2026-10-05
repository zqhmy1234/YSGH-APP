import logging
import os
import re
from typing import Optional, Union

import psycopg
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool

logger = logging.getLogger(__name__)

# 数据库连接超时时间（秒），每次尝试 15 秒，共尝试 2 次
DB_CONNECTION_TIMEOUT = 15
DB_MAX_RETRIES = 2

# 连接池规模（2026-10-05）。
# 下限固定 1：checkpointer 是**懒建**的，服务刚起来时未必有人对话。
# 上限 4：注意 `PostgresSaver._cursor` 全程持`self.lock`，checkpoint 读写
# 本来就是**串行**的 ⇒ 实际同时最多占用 1 条连接，多留的几条用于
# 建表/预热与将来可能的并发读。调大它不会提升吞吐，只增加 DB 连接数。
POOL_MIN_SIZE = 1
POOL_DEFAULT_MAX_SIZE = 4

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


def _env_int(key: str, default: int, *, low: int = 1, high: int = 64) -> int:
    """读整型环境变量并夹紧到 [low, high]（非法值回落默认，不抛）。"""
    try:
        value = int((os.getenv(key) or "").strip() or default)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def _configure_pool_conn(conn: psycopg.Connection) -> None:
    """池里**每一条新连接**都重设 `search_path`（2026-10-05）。

    ## 这是池化唯一真正的难点，其余都是照抄官方用法

    `search_path` 是**连接级会话变量**，不随连接创建自动继承、也不随事务结束重置。
    长驻单连接时代只要在启动时 `SET` 一次就够了；池里每条连接都是**独立的**
    ⇒ 漏设一条，就会在**第一次用到那条连接**时抛
    `relation "checkpoints" does not exist`。

    这个坑特别难查：池按需扩缩，错误时有时无，看起来像"偶发故障"，
    而根因是配置只打在第一条连接上。故放在 `configure` 回调里——
    psycopg_pool 保证它在**每条**连接建好后调用，位置只有一个、没有漏设可能。

    ⚠️ 顺带说明为什么不能省掉 `search_path` 直接用默认 schema：
    表建在 `memory` schema 下（`_setup_schema_and_tables`），
    官方 `from_conn_string` 假设的是 `public`。
    """
    with conn.cursor() as cur:
        cur.execute("SET search_path TO memory")


class MemoryManager:
    """Memory Manager 单例类"""

    _instance: Optional['MemoryManager'] = None
    _checkpointer: Optional[Union[PostgresSaver, MemorySaver]] = None
    # 2026-10-05：由「单条长驻连接」改为「同步连接池」。
    # 原先只有 `_conn`（单连接）且永不重建 —— DB 重启或网络抖动一次后，
    # 进程生命周期内再也不重连，对话记忆静默持续失败（见 get_checkpointer）。
    _pool: Optional[ConnectionPool] = None
    _setup_done: bool = False

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _build_pool(self, db_url: str) -> ConnectionPool:
        """建同步连接池（并等首条连接就绪）。

        ## 三个参数每一个都有坑，不是"照抄示例"而是踩过才知道

        1. `kwargs={"autocommit": True, "prepare_threshold": 0}`
           · `autocommit`：官方 `from_conn_string` 就是 True，saver 的每条语句
             自带事务边界，照抄不会错。
           · `prepare_threshold=0`（**必须**）：psycopg 的服务端预编译语句是
             **连接级**的，池会换连接 —— 复用一条"换来的"连接时，它对应的
             服务端语句并不存在 ⇒ `prepared statement "..." does not exist`。
             这是池化 + psycopg 的经典组合坑，官方同样显式设 0。
        2. `configure=_configure_pool_conn`：逐条设 `search_path`（见该函数说明）。
        3. `check=ConnectionPool.check_connection`：**这就是"断连自愈"的实现**。
           psycopg_pool 在**每次 checkout** 时调用它探活，坏连接当场丢弃并新建
           ⇒ 不存在"断连后要等下一次才恢复"，也不会把坏连接交给 saver 写出
           一堆 `OperationalError`。它只是执行一个空查询，成本可忽略。
           不设的话 psycopg_pool 完全不探活，坏连接会被反复 checkout。
        """
        pool = ConnectionPool(
            db_url,
            min_size=POOL_MIN_SIZE,
            max_size=_env_int("AGENT_MEM_POOL_MAX", POOL_DEFAULT_MAX_SIZE, low=1, high=16),
            timeout=float(DB_CONNECTION_TIMEOUT),
            # 显式 open=False再手动 open()：把"连不上"变成**可捕获**的异常，
            # 从而能降级到 MemorySaver；构造即 open 时错误发生在更早的位置。
            open=False,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "connect_timeout": DB_CONNECTION_TIMEOUT,
            },
            configure=_configure_pool_conn,
            check=ConnectionPool.check_connection,
            name="yishu-agent-memory",
        )
        # wait=True：等到 min_size 条连接就绪才返回（否则首个请求照样超时）。
        # 连不上会抛 PoolTimeout，由调用方降级。
        pool.open(wait=True, timeout=float(DB_CONNECTION_TIMEOUT) * DB_MAX_RETRIES)
        return pool

    def _discard_pool(self) -> None:
        """关掉并清空池 + checkpointer 缓存（自愈与降级的共同收口）。

        **必须真的 `close()`**：只清 Python 侧的引用不会关连接，
        池的后台 worker 线程仍活着 —— 既漏资源，也让下次重建时连接数翻倍。
        """
        pool, self._pool = self._pool, None
        self._checkpointer = None
        if pool is not None:
            try:
                pool.close()
            except Exception as exc:  # noqa: BLE001 —— 关池失败不该阻断调用方
                logger.warning("关闭 memory 连接池失败: %s", exc)

    def _setup_schema_and_tables(self, pool: ConnectionPool) -> bool:
        """同步创建 schema 和表（只执行一次），返回是否成功。

        走**池**而不是一次性连接：`PostgresSaver(pool).setup()` 在池模式下同样
        从池里 checkout，而 `configure` 已经为那条连接设好了 `search_path`。
        """
        if self._setup_done:
            return True

        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("CREATE SCHEMA IF NOT EXISTS memory")
                PostgresSaver(pool).setup()
            self._setup_done = True
            logger.info("Memory schema and tables created")
            return True
        except Exception as e:
            logger.warning(f"Failed to setup schema/tables: {e}")
            return False

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

        ## 2026-10-05：单条长驻连接 → 同步连接池（修「断连不恢复」）

        原实现把一条 `psycopg.Connection` 存进 `_conn` 并**永久复用**。这条连接一旦
        因DB 重启 / 网络抖动 / 防火墙空闲超时而断开，psycopg 不会自己重连，
        而 `_checkpointer` 缓存也永不失效 ⇒ **进程生命周期内再也不重建**，
        对话记忆静默持续失败。更糟的是它**不报错**：`_conn` 从未被close，
        没人知道它已经断了。

        池化同时解决了三件事：
          · 断连自愈 —— `check=check_connection` 在 checkout 时探活，
            坏连接当场丢弃重建（见 `_build_pool`）；
          · `search_path` 逐条生效 —— `configure` 回调（见 `_configure_pool_conn`）；
          · 并发 —— `/v1/chat` 是同步 def，跑在 threadpool 里，多线程会同时
            用 checkpointer。单连接时代靠 `PostgresSaver` 自己的 `threading.Lock`
            串行（**这点原先判断是对的，予以保留**）；池化后每线程各取一条连接，
            仍由那把锁保证 SQL 不交错。
        """
        if self._checkpointer is not None and self._pool is not None:
            return self._checkpointer

        # 1. 尝试获取 db_url（已规范化为原生 psycopg 可解析的形态）
        db_url = self._get_db_url_safe()
        if not db_url:
            return self._create_fallback_checkpointer()

        # 2. 建池（内部重试/超时都由 psycopg_pool 负责，见 _build_pool）
        try:
            pool = self._build_pool(db_url)
        except Exception as e:
            logger.warning(f"连接池创建失败: {e}, will fallback to MemorySaver")
            self._discard_pool()
            return self._create_fallback_checkpointer()

        # ⚠️ 必须**立刻**挂到 `self._pool` 再建表，不能等建表成功后才赋值。
        # 否则建表失败时 `_discard_pool()` 关的是仍为 None 的 `self._pool`，
        # 刚建好的那个池就成了孤儿：没人 close 它，psycopg_pool 的后台 worker
        # 线程（num_workers，默认 3）会一直跑着 —— 而这正是"每次 DB 抖一下
        # 就漏一组线程"的来源。失败路径必须能通过统一的 `_discard_pool` 回收。
        self._pool = pool

        # 3. 建表（走同一个池）。失败即视为本次不可用——**不留任何缓存**，
        #    下次调用会重新建池重试（与 MemorySaver 降级同一口径）。
        if not self._setup_schema_and_tables(pool):
            self._discard_pool()
            return self._create_fallback_checkpointer()

        self._checkpointer = PostgresSaver(pool)
        logger.info("PostgresSaver initialized successfully (对话上下文将持久化到 Postgres)")
        return self._checkpointer

    def checkpointer_kind(self) -> str:
        """当前 checkpointer 的真实类型（供 /health 暴露，避免降级对用户不可见）。

        顺带带上**池的只读统计**（`get_stats()` 不建连、不改状态，符合
        「探活不得触发建连」的硬约束）：池因为反复重连而connections 一直上不去，
        是"DB 在抖"的直接信号—— 而降级与否只有在这里能看到。
        """
        if self._checkpointer is None:
            return "未初始化"
        if isinstance(self._checkpointer, PostgresSaver):
            return f"PostgresSaver（持久化·{self._pool_stats()}）"
        if isinstance(self._checkpointer, MemorySaver):
            return "MemorySaver（降级·不持久）"
        return type(self._checkpointer).__name__

    def _pool_stats(self) -> str:
        """连接池的只读统计，形如 `pool=2/4 lost=0`。**只读**：不 checkout、不建连。

        ⚠️ 键名必须与 psycopg_pool 的 `get_stats()` **实际**返回对齐。
        这里踩过一次：原先写的是 `connections_size`，而真实返回里根本没有这个键
        （实际是 `pool_size` / `pool_max`），于是 /health 一直显示 `pool=?/4`。
        桩测试用自己造的 dict断言，照样绿 —— **只有真机才抓得到这类键名错误**。

        `lost` 是 `connections_lost`（累计丢弃的坏连接数）：它持续增长就等于
        "DB 在反复断连"，是比"当前连接数"更早的告警信号。
        """
        pool = self._pool
        if pool is None:
            return "pool=none"
        try:
            stats = pool.get_stats()
            return (
                f"pool={stats.get('pool_size', '?')}/{stats.get('pool_max', '?')}"
                f" lost={stats.get('connections_lost', 0)}"
            )
        except Exception:  # noqa: BLE001,S110 —— 统计取不到不该让/health 500
            return "pool=?"

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


def close_memory_pool() -> None:
    """关掉连接池并清空 checkpointer 缓存（供服务 lifespan 退出时调用）。

    为什么必须有：psycopg_pool 有**后台 worker 线程**（`num_workers`，默认 3）
    持续维护池大小。不close 的话，进程虽退出，但优雅关闭 / 测试里反复建池时
    会攒下多个仍在跑线程的池 —— 表现为测试越来越慢、连接数只涨不跌。

    单连接时代没有这个问题（一条连接随对象回收），这正是池化后新增的义务。
    """
    global _memory_manager
    if _memory_manager is not None:
        _memory_manager._discard_pool()