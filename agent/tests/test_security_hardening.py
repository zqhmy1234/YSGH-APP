"""2026-10-05 安全加固的回归测试。

本轮修的都是**不会让测试变红、只会悄悄泄漏或悄悄放行**的缺陷，因此必须有测试钉住：

1. 错误响应不再回显异常原文（可能含数据库 DSN、绝对路径、SQL）。
2. 敏感字段守卫不再被 `_id` 命名绕过（`secret_id` 这类名字曾一行放行）。
3. 限流桶会被回收（`forget_idle` 曾只有定义、全仓无调用方）。
4. 收藏搜索的 `ilike` 通配口径与limit 夹紧。
5. 敏感词守卫的判定顺序（量词后缀不得成为绕过口）。
6. `user_id` / `thread_id` 正则用 `fullmatch`（`$` 会放过结尾换行）。

⚠️ 原第4 项「`upload_from_url` 拒绝 file:// 与内网地址」已随
`storage/s3/s3_storage.py` 一起删除（2026-10-05 第二个窗口）：
该文件是 **Coze 原有**的零调用方死副本，加固它等于给没人用的代码做安全投资。
真正的出网面是 `yishu/fetch.py`，其SSRF 防护由
`test_memory_resilience.py` 与 fetch 自身的重定向逐跳复校验覆盖。
"""
from __future__ import annotations

import pytest

from main import _client_message
from yishu.errors import ClassifiedError
from yishu.observability import SensitiveFieldError, log_event
from yishu.security import RateLimiter


# ── 1. 错误响应不得回显异常原文 ─────────────────────────────────────
@pytest.mark.parametrize(
    "kind",
    ["config", "auth", "rate_limit", "timeout", "upstream", "bad_input", "internal", "未知类别"],
)
def test_client_message_never_leaks_exception_text(kind: str):
    """对外文案必须是固定中文短语，不能是 str(exc)。

    真实风险样本：`postgresql+psycopg://user:pass@host/db`、服务器绝对路径、SQL 片段。
    """
    msg = _client_message(ClassifiedError(kind, 500, False))
    assert "://" not in msg
    assert "Traceback" not in msg
    assert "select" not in msg.lower()
    assert "D:\\" not in msg and "/home/" not in msg and "/Users/" not in msg
    # 必须是可读中文短语，而不是空串或类名
    assert msg and any("一" <= ch <= "鿿" for ch in msg)


# ── 2. 敏感字段守卫不可被命名绕过 ───────────────────────────────────
@pytest.mark.parametrize("key", ["secret_id", "api_key_id", "token_value_id", "authorization_id"])
def test_sensitive_guard_not_bypassed_by_id_suffix(key: str):
    """`*_id` 后缀曾让敏感词检查直接 return —— 守卫可被命名绕过等于没有守卫。"""
    with pytest.raises(SensitiveFieldError):
        log_event("probe", **{key: "whatever"})


@pytest.mark.parametrize(
    "key",
    ["user_id", "thread_id", "memory_id", "msg_len", "latency_ms",
     # W1 镜像链路要对账 contents 行的 UUID，content_id 必须可记
     "content_id"],
)
def test_safe_identifier_and_quantifier_fields_allowed(key: str):
    """正常的标识符/量词字段不能被误伤（误伤会逼调用方吞掉异常→埋点静默失效）。

    `content_id` 是 2026-10-06 加入白名单的：它是 contents 行的 **UUID 标识符**，
    W1 镜像链路（mirror_ok）要对账它。实测教训：没加白名单时，镜像本身成功、
    但 `log_event(mirror_ok, content_id=...)` 抛 SensitiveFieldError 被上层吞掉
    ⇒ 日志里这条镜像"凭空消失"，对账无从下手。
    """
    log_event("probe", **{key: "x" if key.endswith(("id", "ids")) else 1})


@pytest.mark.parametrize(
    "key", ["secret_at", "api_key_at", "password_count", "secret_ms", "authorization_len"]
)
def test_quantifier_suffix_fields_are_exempt_by_construction(key: str):
    """量词后缀豁免是**语义正确**的，不是绕过。

    `_len`/`_count`/`_ms`/`_chars`/`_at` 的值是长度/计数/耗时/时间戳，
    结构上装不下秘密 —— `secret_at` 存的是时间戳，不是密钥。

    ⚠️ 本用例是一次**回退**留下的护栏：2026-10-05 曾把判定顺序改成
    「敏感词优先」，想让 `secret_at` 也被拦，结果打死了真实埋点
    `memory_tools.save_memory_dedup_miss(content_len=...)`（命中 `content`），
    4 个存量测试当场变红、判重日志静默失效。
    真正需要防的命名绕过是 `_id`（`secret_id` 曾一行放行），
    那个已由 `_SAFE_ID_KEYS` **显式白名单**堵住；不要动这张表的判定顺序。
    """
    log_event("probe", **{key: 1})


def test_content_len_must_not_be_rejected():
    """`content_len` 是生产代码在用的真实埋点字段，被误伤就会让判重日志静默失效。"""
    log_event("save_memory_dedup_miss", content_len=42, window_min=10, rows_in_window=3)


@pytest.mark.parametrize("key", ["secret", "secret_value", "password", "api_key",
                                 "token_value", "prompt", "raw", "authorization"])
def test_sensitive_words_are_still_rejected(key: str):
    """量词豁免不得扩大到整个敏感词表。"""
    with pytest.raises(SensitiveFieldError):
        log_event("probe", **{key: "whatever"})


# ── 3b. 标识符/值校验必须用 fullmatch ───────────────────────────────
def test_identifier_regexes_are_anchored_with_fullmatch_semantics():
    """`^...$` 只锚定**末尾**：`re.match(r"^[A-Za-z0-9_-]+$", "abc\\n")` 为真。

    注意**通过 `validate_*` 观察不到这个差异**——那两个函数都先 `.strip()`，
    换行已被去掉。本用例直接锁正则契约，断言的是"以后有人把 fullmatch
    改回 match 会被发现"，属于防御性护栏而非当前可观测行为。
    """
    from yishu.security import _THREAD_ID_RE, _USER_ID_RE

    assert _USER_ID_RE.fullmatch("abc\n") is None
    assert _THREAD_ID_RE.fullmatch("t\n") is None
    # 正常值仍然通过（别把护栏做成误伤源）
    assert _USER_ID_RE.fullmatch("550e8400-e29b-41d4-a716-446655440000")
    assert _THREAD_ID_RE.fullmatch("user-1:session-9")


@pytest.mark.parametrize("bad", ["a/b", "a\\b", "a.b", "a b", "", "x" * 65, "../etc"])
def test_user_id_format_rejects_unsafe_values(bad: str):
    """路径穿越 / SQL 片段 / 超长 / 空值一律 400。"""
    from fastapi import HTTPException

    from yishu.security import validate_user_id

    with pytest.raises(HTTPException):
        validate_user_id(bad)


# ── 3. 限流桶回收 ──────────────────────────────────────────────────
def test_rate_limiter_reclaims_idle_buckets():
    """无命中记录的桶（长期不活跃）应被回收，而不是随 user_id 无限增长。"""
    limiter = RateLimiter(max_calls=5, window_s=60.0)
    for i in range(20):
        assert limiter.check(f"u{i}")[0] is True
    assert len(limiter._buckets) == 20

    for bucket in limiter._buckets.values():
        bucket.hits.clear()  # 模拟"自打上次的窗口起就没再来过"
    limiter.forget_idle(max_idle_buckets=1)
    assert len(limiter._buckets) == 1


def test_rate_limiter_never_reclaims_active_buckets():
    """正在使用的桶**绝不能**被回收——否则用户会被无理由地限流。"""
    limiter = RateLimiter(max_calls=5, window_s=60.0)
    for i in range(5):
        limiter.check(f"u{i}")
    limiter.forget_idle(max_idle_buckets=1)
    assert len(limiter._buckets) == 5


def test_rate_limiter_check_triggers_reclaim(monkeypatch):
    """check() 必须顺带触发回收（这是它唯一的调用点）。"""
    limiter = RateLimiter(max_calls=5, window_s=60.0)
    calls = {"n": 0}
    real = limiter.forget_idle

    def spy(*a, **k):
        calls["n"] += 1
        return real(*a, **k)

    monkeypatch.setattr(limiter, "forget_idle", spy)
    limiter.check("someone")
    assert calls["n"] >= 1


# ── 4. 收藏搜索口径 ────────────────────────────────────────────────
def test_collection_search_uses_postgrest_wildcard(monkeypatch):
    """`%{query}%` 会被 `_pg_pattern` 转义成字面量 ⇒ 收藏搜索实际一直是坏的。"""
    import tools.memory_tools as mt

    seen: dict = {}

    class _Q:
        def ilike(self, column, pattern):
            seen["pattern"] = pattern
            return self

        def eq(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def limit(self, n):
            seen["limit"] = n
            return self

        def execute(self):
            class _R:
                data = []
            return _R()

    class _T:
        def select(self, *a, **k):
            return _Q()

        def eq(self, *a, **k):
            return _Q()

    monkeypatch.setattr(mt, "_get_client", lambda: type("C", (), {"table": lambda _s, _t: _T()})())
    monkeypatch.setattr(mt, "_get_user_id", lambda: "u1")
    mt.search_knowledge_collections.invoke({"query": "灯", "limit": 99999})
    assert seen["pattern"] == "*灯*"
    assert seen["limit"] == 50  # 夹紧，不接受模型给的 99999
