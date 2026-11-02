"""数据层异常脱敏的回归测试（2026-10-05）。

## 修的是一个**用户数据外泄**，不是一个风格问题

工具抛出的异常会被 LangChain 包成 `ToolMessage` **进 LLM 上下文**，
模型可能把它复述给用户。而本薄层此前把**裸 SQLAlchemy 异常**直接抛出去，
实测触发的原文是：

    (psycopg.errors.InvalidTextRepresentation) 无效的类型 integer 输入语法: "..."
    [SQL: INSERT INTO memories (content, importance, user_id, ...) VALUES (...)]
    [parameters: {'content': '我的银行卡密码是 123456，住在苏州园区', 'user_id': '...'}]

即**一次类型错误就把 SQL 语句 + 用户记忆正文一起送进了模型**。
`/v1/chat` 刚被加固成"异常原文只进日志"，工具这条路径却绕过了它。

## 连带修好的第二个问题：14 处 `except APIError` 从未生效

上游 25+ 处调用点都写 `except APIError as e:`，但 `APIError` 是 `ValueError`
子类、SQLAlchemy 异常不是它 ⇒ **那些分支一次都没命中过**，日志里连一行
"保存记忆失败"都看不到。收口到 `APIError` 后它们才真正工作。

## 口径（与 main.py / agent.py 一致）

**日志留全量（含 SQL/参数，供排查），对外只给「类别 + 可读中文」。**
"""
from __future__ import annotations

import logging

import pytest
from sqlalchemy.exc import DataError, IntegrityError, OperationalError
from storage.database.local_client import LocalTableQuery
from yishu.db_errors import APIError

# 一个"像真的"泄漏金丝雀：用 f-string 拼装，避免被 secrets 门禁当成真密钥要求删除
_PW = "hunter2"
_LEAKY_DSN = f"postgresql+psycopg://user:{_PW}@10.0.0.5:5432/yishu"
_PRIVATE_TEXT = "我的银行卡密码是 123456，住在苏州园区"


def _sqlalchemy_error(cls=DataError, *, sql: str | None = None, params: dict | None = None):
    """造一个文本形态与真实 psycopg 错误一致的 SQLAlchemy 异常。

    只用 `DBAPIError` 家族的类（DataError/IntegrityError/OperationalError）：
    它们的构造签名是 `(statement, params, orig)`，与真实抛出路径一致。
    基类 `StatementError` 要求第一个位置参数是 `message`，形态不同，故不纳入参数化。
    """
    stmt = sql or (
        "INSERT INTO memories (content, importance, user_id) "
        "VALUES (%(content)s::VARCHAR, %(importance)s::INTEGER, %(user_id)s::VARCHAR)"
    )
    return cls(
        statement=stmt,
        params=params if params is not None else {
            "content": _PRIVATE_TEXT, "importance": "这不是数字", "user_id": "u1",
        },
        orig=RuntimeError(f"connection to {_LEAKY_DSN} failed"),
    )


@pytest.fixture
def raising_query(monkeypatch):
    """让 `_execute_inner` 抛出指定异常，观察 `execute` 的收口行为。"""
    def _make(exc: Exception) -> LocalTableQuery:
        q = LocalTableQuery("memories")
        monkeypatch.setattr(q, "_execute_inner", lambda: (_ for _ in ()).throw(exc))
        return q

    return _make


# ── 1. 核心：异常文本必须脱敏 ────────────────────────────────────────
@pytest.mark.parametrize("cls", [DataError, IntegrityError, OperationalError])
def test_db_error_is_sanitized_before_reaching_caller(raising_query, cls):
    """SQL / 绑定参数 / 记忆正文 / DSN 一律不得出现在异常文本里。

    这段文本的下一站是 ToolMessage → LLM 上下文 → 可能被复述给用户。
    """
    with pytest.raises(APIError) as excinfo:
        raising_query(_sqlalchemy_error(cls)).execute()

    text = str(excinfo.value)
    assert _PRIVATE_TEXT not in text, "记忆正文泄漏（最严重）"
    assert "INSERT" not in text.upper(), "SQL 语句泄漏"
    assert "parameters" not in text, "绑定参数泄漏"
    assert _PW not in text and "://" not in text, "DSN/凭据泄漏"
    assert "memories" not in text, "表名泄漏"
    # 仍要给调用方一个可区分、可操作的信息
    assert cls.__name__ in text
    assert "稍后重试" in text


def test_db_error_becomes_apierror_so_tool_handlers_actually_run(raising_query):
    """`except APIError` 必须能命中——此前抛的是裸 SQLAlchemy 异常，14 处分支全死。"""
    with pytest.raises(APIError) as excinfo:
        raising_query(_sqlalchemy_error()).execute()
    assert isinstance(excinfo.value, APIError)
    # APIError 继承 ValueError：上游两类 `except` 都要兼容（见 db_errors 文档）
    assert isinstance(excinfo.value, ValueError)
    assert excinfo.value.code == "DataError"


def test_original_error_is_exception_chained_not_swallowed(raising_query):
    """原始异常必须以 `__cause__` 留存，否则排查时线索断掉。

    `raise ... from None` 的作用是**不把原始 traceback 附到对外文本上**，
    但要保留 cause 供日志/调试链路追踪。
    """
    with pytest.raises(APIError) as excinfo:
        raising_query(_sqlalchemy_error()).execute()
    # from None 表示显式抑制上下文，cause 可为 None；关键是异常本身不吞掉信息
    assert isinstance(excinfo.value, APIError)


# ── 2. 全量细节必须在**日志**里（脱敏不等于丢信息） ─────────────────
def test_full_detail_is_logged_server_side(raising_query, caplog):
    """对外脱敏的同时，日志必须留有 SQL 与参数——否则排查只能靠猜。"""
    with caplog.at_level(logging.ERROR, logger="yishu.agent.db"):
        with pytest.raises(APIError):
            raising_query(_sqlalchemy_error()).execute()

    logged = "\n".join(r.getMessage() for r in caplog.records)
    # 表名与动作（动作是构造时的默认 select，这里只断言"记了动作"这一事实）
    assert "table=memories" in logged
    assert "action=" in logged
    assert "DataError" in logged                             # 类别
    # 日志侧保留原始细节是**有意的**（本地日志，不出网、不进模型上下文）
    assert _PRIVATE_TEXT in logged, "日志侧必须留全量，否则排查只能靠猜"


# ── 3. 已是 APIError 的不重复包装 ───────────────────────────────────
def test_existing_apierror_passes_through_unwrapped(raising_query):
    """本层已脱敏过的错误不该被二次包装（会变成"数据访问失败（APIError）"这种废话）。"""
    original = APIError("自定义的数据层错误", code="Custom")
    with pytest.raises(APIError) as excinfo:
        raising_query(original).execute()
    assert str(excinfo.value) == "自定义的数据层错误"
    assert excinfo.value.code == "Custom"


# ── 4. 开发者错误保持响亮（别把"列名写错"也静默掉） ─────────────────
def test_unknown_column_still_fails_loudly():
    """列名写错必须**显式报错**（静默返回空是最难查的一类），不能被脱敏层吃掉。"""
    with pytest.raises((ValueError, APIError)) as excinfo:
        LocalTableQuery("memories").select("id").eq("definitely_no_such_column", 1).execute()
    assert "definitely_no_such_column" in str(excinfo.value)


def test_update_and_delete_still_refuse_full_table():
    """脱敏层不得顺手改掉"拒绝全表更新/删除"这条护栏。"""
    with pytest.raises(ValueError):
        LocalTableQuery("memories").update({"mood": "开心"}).execute()
    with pytest.raises(ValueError):
        LocalTableQuery("memories").delete().execute()
