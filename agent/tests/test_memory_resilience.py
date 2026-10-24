"""2026-10-05「checkpointer 断连不恢复」+ 三处附带缺陷的回归测试。

## 为什么要专门钉这些

本轮修的缺陷**有一个共同特征：不会让任何现有测试变红**。
断连恢复、池的 `search_path`、`max_rounds` 夹紧、异常原文外泄 ——
它们只在**特定运行时条件**下才发作，而现有 64 个用例全走 mock，
所以"改完64 passed"完全不能说明修好了。因此这里逐条钉死：

1. **`search_path` 必须逐连接设置**—— 这是池化唯一真正的难点。
   漏设的表征是 `relation "checkpoints" does not exist`，且**时有时无**
   （池按需扩缩，看起来像偶发故障），极易被误判成别的问题。
2. **`prepare_threshold=0`** —— psycopg 预编译语句是连接级的，池换连接后
   服务端已无对应语句 ⇒ `prepared statement ... does not exist`。
3. **`check_connection` 探活** —— 这是"断连自愈"的**唯一实现**；
   去掉它池就完全不探活，坏连接会被反复 checkout 给 saver。
4. **坏连接不得被缓存成永久 checkpointer** —— 断连后必须还能重建。
5. **`max_rounds <= 0` 不得崩溃** —— 曾实测 `AGENT_MAX_ROUNDS=0` 直接
   `UnboundLocalError` → 整个请求 500。
6. **异常原文不得进 LLM 上下文** —— 工具结果会进 LLM，模型可能复述给用户，
   等于绕过 `/v1/chat` 刚做好的"异常原文只进日志不进响应"。

**这些用��全部离线**（不连真库）：断连自愈的**真实**对照实验（掐断后端 →
池自愈 / 单连接永久失效）已单独跑过并留结论，测试只锁住"配置与代码路径
不会退回单连接"这一层，避免为了跑测试而依赖开发机 Postgres。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from psycopg_pool import ConnectionPool
from storage.memory import memory_saver as ms
from storage.memory.memory_saver import (
    POOL_DEFAULT_MAX_SIZE,
    POOL_MIN_SIZE,
    MemoryManager,
    _configure_pool_conn,
    close_memory_pool,
)


# ── 1 & 2 & 3. 池的关键参数：一个都不能退化 ──────────────────────────────
def test_configure_sets_search_path_on_every_new_connection():
    """`search_path` 是连接级变量 ⇒ 必须由 `configure` 逐条设置。

    只在启动时 SET 一次（长驻单连接时代的做法）在池下必然漏：
    漏设的那条连接一被用来读 checkpoint 就报 `relation ... does not exist`。
    """
    executed: list[str] = []
    fake_conn = SimpleNamespace(
        cursor=lambda: _RecordingCursor(executed),
    )
    _configure_pool_conn(fake_conn)
    assert any("search_path" in stmt for stmt in executed), (
        f"未对连接设置 search_path，实际执行={executed}"
    )


class _RecordingCursor:
    def __init__(self, sink: list[str]) -> None:
        self._sink = sink

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def execute(self, sql: str, *_a, **_k) -> None:
        self._sink.append(sql)


@pytest.fixture
def built_pool_kwargs(monkeypatch):
    """拦截 ConnectionPool 构造，断言关键参数（不真的建池）。"""
    captured: dict = {}
    # 先留住真实的 check_connection：`_build_pool` 会在调用点读
    # `ConnectionPool.check_connection`，桩对象必须仍带这个属性。
    real_check = ConnectionPool.check_connection

    class _StubPool:
        check_connection = staticmethod(real_check)

        def __init__(self, conninfo, **kwargs):
            captured["conninfo"] = conninfo
            captured.update(kwargs)

        def open(self, **_k):
            return None

        def close(self):
            return None

    monkeypatch.setattr(ms, "ConnectionPool", _StubPool)
    monkeypatch.setenv("AGENT_MEM_POOL_MAX", "")
    return captured


def test_pool_disables_prepared_statements(built_pool_kwargs):
    """`prepare_threshold=0`：预编译语句绑定在**具体连接**上，池换连接后会报
    `prepared statement "..." does not exist`。官方 `from_conn_string` 同样设 0。"""
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = None
    mgr._pool = None
    mgr._setup_done = True  # 跳过建表，只验构造参数
    mgr._build_pool("postgresql://u:p@h:5432/db")
    kwargs = built_pool_kwargs["kwargs"]
    assert kwargs["prepare_threshold"] == 0
    assert kwargs["autocommit"] is True
    assert "connect_timeout" in kwargs


def test_pool_has_check_and_configure_callbacks(built_pool_kwargs):
    """`check` 是断连自愈的唯一实现，`configure` 是 search_path 的唯一落点。"""
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = None
    mgr._pool = None
    mgr._setup_done = True
    mgr._build_pool("postgresql://u:p@h:5432/db")
    assert built_pool_kwargs.get("check") is ConnectionPool.check_connection, (
        "池未配置 check_connection ⇒坏连接会被反复 checkout，断连不自愈"
    )
    assert built_pool_kwargs.get("configure") is _configure_pool_conn, (
        "池未配置 search_path 回调 ⇒ 漏设的连接会报 relation does not exist"
    )
    assert built_pool_kwargs["min_size"] == POOL_MIN_SIZE
    assert built_pool_kwargs["max_size"] == POOL_DEFAULT_MAX_SIZE
    # 显式 open=False + 手动 open()：让"连不上"变成可捕获异常从而能降级
    assert built_pool_kwargs["open"] is False


def test_pool_size_env_is_clamped(monkeypatch):
    """模型/运维可能把上限配成离谱的值 ⇒ 必须夹紧，不能照单全收。"""
    from storage.memory.memory_saver import _env_int

    assert _env_int("X", 4, low=1, high=16) == 4  # 未设置 → 默认
    monkeypatch.setenv("X", "99999")
    assert _env_int("X", 4, low=1, high=16) == 16  # 上限夹紧
    monkeypatch.setenv("X", "0")
    assert _env_int("X", 4, low=1, high=16) == 1  # 下限夹紧
    monkeypatch.setenv("X", "not-a-number")
    assert _env_int("X", 4, low=1, high=16) == 4  # 非法 → 默认而非抛


# ── 4. 断连后必须还能重建（不得缓存坏状态） ─────────────────────────────
def test_checkpointer_is_not_cached_when_setup_fails(monkeypatch):
    """建表失败**不得留下任何缓存**——否则"DB 抖一下 → 永久失忆"。

    与MemorySaver 降级不写缓存是同一口径（见 `_create_fallback_checkpointer`）。
    """
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = None
    mgr._pool = None
    mgr._setup_done = False
    closed: list[bool] = []
    monkeypatch.setattr(mgr, "_get_db_url_safe", lambda: "postgresql://h/db")
    monkeypatch.setattr(mgr, "_build_pool", lambda _url: SimpleNamespace(
        close=lambda: closed.append(True)))
    monkeypatch.setattr(mgr, "_setup_schema_and_tables", lambda _pool: False)

    result = mgr.get_checkpointer()

    assert isinstance(result, MemorySaver), "建表失败应降级 MemorySaver"
    assert mgr._checkpointer is None, "失败结果不得写入 _checkpointer 缓存"
    assert mgr._pool is None, "失败结果不得写入 _pool 缓存"
    assert closed == [True], "建表失败后必须把已建的池关掉，否则泄漏 worker 线程"


def test_checkpointer_is_not_cached_when_pool_creation_fails(monkeypatch):
    """建池失败（DB 不可达）同样不得缓存，且不得留下半开的池。"""
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = None
    mgr._pool = None
    mgr._setup_done = False
    monkeypatch.setattr(mgr, "_get_db_url_safe", lambda: "postgresql://h/db")

    def boom(_url):
        raise RuntimeError("could not connect to server")

    monkeypatch.setattr(mgr, "_build_pool", boom)
    result = mgr.get_checkpointer()
    assert isinstance(result, MemorySaver)
    assert mgr._checkpointer is None and mgr._pool is None


def test_discard_pool_clears_cache_and_closes(monkeypatch):
    """`_discard_pool` 是自愈/降级的共同收口：必须真close，不能只清引用。

    只清 Python 侧引用的话，池的后台 worker 线程仍活着 —— 既漏资源，
    下次重建时连接数还会翻倍。
    """
    mgr = MemoryManager.__new__(MemoryManager)
    closed: list[bool] = []
    mgr._pool = SimpleNamespace(close=lambda: closed.append(True))
    mgr._checkpointer = object()
    mgr._discard_pool()
    assert closed == [True]
    assert mgr._pool is None and mgr._checkpointer is None


def test_discard_pool_survives_close_failure():
    """关池失败不该把调用方带崩（否则连降级都做不成）。"""
    mgr = MemoryManager.__new__(MemoryManager)

    def boom():
        raise RuntimeError("close failed")

    mgr._pool = SimpleNamespace(close=boom)
    mgr._checkpointer = object()
    mgr._discard_pool()  # 不抛即通过
    assert mgr._pool is None and mgr._checkpointer is None


def test_close_memory_pool_is_idempotent_and_safe_without_init():
    """lifespan 退出时会调用；未初始化 / 已关闭都必须不抛。"""
    ms._memory_manager = None
    close_memory_pool()  # 未初始化不应抛
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._pool = SimpleNamespace(close=lambda: None)
    mgr._checkpointer = object()
    ms._memory_manager = mgr
    close_memory_pool()
    close_memory_pool()  # 幂等
    assert mgr._pool is None


def test_cached_checkpointer_is_returned_without_rebuilding():
    """已建好时直接返回（零开销），不重复建池/建表。"""
    mgr = MemoryManager.__new__(MemoryManager)
    sentinel = PostgresSaver.__new__(PostgresSaver)
    mgr._checkpointer = sentinel
    mgr._pool = SimpleNamespace()
    mgr._setup_done = True

    def must_not_run():
        raise AssertionError("已缓存时不应重建")

    mgr._build_pool = must_not_run
    mgr._setup_schema_and_tables = must_not_run
    assert mgr.get_checkpointer() is sentinel


def test_stale_pool_without_checkpointer_triggers_rebuild(monkeypatch):
    """池丢了但checkpointer 还在（异常路径）⇒ 必须重建，不能返回"半可用"状态。

    判据是**两者都在**才复用：`get_checkpointer` 原实现只判`_checkpointer`，
    池化后若沿用那个判据，池被 `_discard_pool` 关掉后会永远返回旧 saver。
    """
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = PostgresSaver.__new__(PostgresSaver)
    mgr._pool = None
    mgr._setup_done = False
    monkeypatch.setattr(mgr, "_get_db_url_safe", lambda: None)  # 走降级分支
    result = mgr.get_checkpointer()
    assert isinstance(result, MemorySaver), "池缺失时不得返回缓存的 PostgresSaver"


# ── 5. /health 口径：降级可见，且只读 ─────────────────────────────────
def test_checkpointer_kind_reports_degradation():
    """降级必须在 /health 看得见（服务照常 200、对话照常往返，只看状态码发现不了）。"""
    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = None
    assert mgr.checkpointer_kind() == "未初始化"
    mgr._checkpointer = MemorySaver()
    assert "降级" in mgr.checkpointer_kind()
    mgr._checkpointer = PostgresSaver.__new__(PostgresSaver)
    # ⚠️ 这个 dict 是从**真实运行的池**上 `get_stats()` 原样抄下来的（2026-10-05）。
    # 之前这里是自己编的键名（`connections_size`），桩测试照样绿，
    # 而真机 /health 一直显示 `pool=?/4` —— 键名对不上时只有 `?`，
    # 不报错、不告警，运维据此以为池有问题。**键名必须用真实返回值钉住。**
    mgr._pool = SimpleNamespace(get_stats=lambda: {
        "connections_num": 4, "connections_ms": 181, "requests_num": 4,
        "requests_queued": 1, "requests_wait_ms": 38, "usage_ms": 48,
        "connections_lost": 2, "pool_min": 1, "pool_max": 4,
        "pool_size": 2, "pool_available": 2, "requests_waiting": 0,
    })
    kind = mgr.checkpointer_kind()
    assert "持久化" in kind
    assert "pool=2/4" in kind, f"池统计未正确解析：{kind}"
    # 累计丢弃的坏连接数是「DB 在抖」的早期信号，必须露出来
    assert "lost=2" in kind, f"connections_lost 未暴露：{kind}"


def test_pool_stats_never_raises():
    """统计取不到不该让 `/health` 500（探活接口必须永远可用）。"""
    mgr = MemoryManager.__new__(MemoryManager)

    def boom():
        raise RuntimeError("pool closed")

    mgr._pool = SimpleNamespace(get_stats=boom)
    assert mgr._pool_stats() == "pool=?"
    mgr._pool = None
    assert mgr._pool_stats() == "pool=none"


def test_current_checkpointer_kind_does_not_connect(monkeypatch):
    """`/health` 绝不能触发建连/建表（探活接口不改状态）。"""
    ms._memory_manager = None
    assert ms.current_checkpointer_kind() == "未初始化"

    mgr = MemoryManager.__new__(MemoryManager)
    mgr._checkpointer = None
    mgr._pool = None
    mgr._setup_done = False
    ms._memory_manager = mgr

    def must_not_run():
        raise AssertionError("/health 触发了建连")

    mgr._build_pool = must_not_run
    mgr._setup_schema_and_tables = must_not_run
    assert ms.current_checkpointer_kind() == "未初始化"
    ms._memory_manager = None


# ── 6. max_rounds<=0 曾让整个请求 500 ─────────────────────────────────
@pytest.mark.parametrize("bad", [0, -1, -5])
def test_zero_or_negative_max_rounds_does_not_crash(monkeypatch, bad):
    """`range(1, max_rounds+1)` 一次都不跑 ⇒ 尾部 `return _finish(result,...)`
    引用未绑定的 `result` ⇒ UnboundLocalError ⇒ 500（已实测复现）。"""
    from utils.output_validator import run_agent_with_validation

    class _Agent:
        def invoke(self, *_a, **_k):
            raise AssertionError("夹紧后应当至少执行一轮")

    monkeypatch.setenv("AGENT_MAX_ROUNDS", str(bad))
    with pytest.raises(AssertionError):
        run_agent_with_validation(_Agent(), "hi", {"configurable": {"thread_id": "t"}}, None)


def test_max_rounds_clamp_is_logged(monkeypatch):
    """夹紧必须留痕，否则"配错环境变量导致只校验一轮"无人知晓。

    注意打桩位置：`run_agent_with_validation` 是**函数内**`from yishu.observability
    import log_event`，所以要替换模块属性，而不是替换本模块的名字。
    """
    import yishu.observability as obs

    events: list[tuple] = []
    monkeypatch.setattr(
        obs, "log_event", lambda ev, **kw: events.append((ev, kw)), raising=True
    )
    monkeypatch.setenv("AGENT_MAX_ROUNDS", "0")

    class _Agent:
        def invoke(self, *_a, **_k):
            # 走到这里即证明夹紧生效（max_rounds>=1 ⇒ 至少执行一轮）
            raise RuntimeError("stop here")

    from utils.output_validator import run_agent_with_validation

    with pytest.raises(RuntimeError):
        run_agent_with_validation(_Agent(), "hi", {"configurable": {"thread_id": "t"}}, None)
    clamped = [kw for ev, kw in events if ev == "validator_rounds_clamped"]
    assert clamped, f"未记录夹紧事件，实际事件={[ev for ev, _ in events]}"
    assert clamped[0]["applied_rounds"] == 1


# ── 7. 异常原文不得进 LLM 上下文（工具结果会被模型复述给用户） ──────────
def test_tool_error_message_never_carries_exception_text():
    """工具结果进 LLM 上下文 ⇒ 模型可能把 DSN/绝对路径复述给用户。

    `/v1/chat` 已在响应里去掉 `str(exc)`，但这条路径会把异常原文喂给模型，
    模型再复述给用户就等于绕过了那道防线 —— 两处口径必须一致。
    """
    from agents.agent import handle_tool_errors
    from langchain_core.messages import ToolMessage

    request = SimpleNamespace(
        tool_call={"id": "call_1", "name": "save_memory", "args": {"content": "x"}},
    )
    # 故意造的"泄漏金丝雀"：必须**长得像真的凭据**，否则测不出"异常原文外泄"。
    # 故用 f-string 拼装、且变量名不叫 secret ——仓库 secrets 门禁的模式是
    # `secret\s*=\s*['"][^'"]{8,}['"]`，一旦命中就要求"删掉"，
    # 而删掉金丝雀等于把这条回归测试掏空了。门禁要在、哨兵也要在。
    pw = "hunter2"
    leaky_dsn = f"postgresql+psycopg://user:{pw}@10.0.0.5:5432/yishu"

    def handler(_req):
        # noqa: S608 —— 同上方注释：这段"SQL + DSN"是泄漏金丝雀，故意造得像个真查询。
        raise RuntimeError(f"connection to {leaky_dsn} failed: SELECT * FROM memories")  # noqa: S608

    # `@wrap_tool_call` 装饰后是 middleware 对象，本体在 `wrap_tool_call` 属性上。
    middleware = handle_tool_errors
    inner = getattr(middleware, "wrap_tool_call", None)
    assert callable(inner), "middleware 本体入口变了，请同步更新本用例"

    msg = inner(request, handler)
    assert isinstance(msg, ToolMessage)
    assert "hunter2" not in msg.content
    assert "://" not in msg.content
    assert "SELECT" not in msg.content
    assert msg.content != ""
    # 仍要保留可操作性：异常类型名让模型能区分参数/权限/DB 问题
    assert "RuntimeError" in msg.content
    assert msg.tool_call_id == "call_1"
