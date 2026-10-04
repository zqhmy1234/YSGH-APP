"""2026-10-05 安全加固的回归测试。

本轮修的都是**不会让测试变红、只会悄悄泄漏或悄悄放行**的缺陷，因此必须有测试钉住：

1. 错误响应不再回显异常原文（可能含数据库 DSN、绝对路径、SQL）。
2. 敏感字段守卫不再被 `_id` 命名绕过（`secret_id` 这类名字曾一行放行）。
3. 限流桶会被回收（`forget_idle` 曾只有定义、全仓无调用方）。
4. `upload_from_url` 拒绝 `file://` 与内网地址。
5. 收藏搜索的 `ilike` 通配口径与 limit 夹紧。
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


@pytest.mark.parametrize("key", ["user_id", "thread_id", "memory_id", "msg_len", "latency_ms"])
def test_safe_identifier_and_quantifier_fields_allowed(key: str):
    """正常的标识符/量词字段不能被误伤（误伤会逼调用方吞掉异常→埋点静默失效）。"""
    log_event("probe", **{key: "x" if key.endswith(("id", "ids")) else 1})


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


# ── 4. upload_from_url 的 SSRF/LFI 防护 ────────────────────────────
def test_upload_from_url_rejects_file_scheme():
    """`file://` 曾能把 /etc/passwd 读出来并上传到 S3（再经预签名 URL 外泄）。"""
    from storage.s3.s3_storage import S3SyncStorage

    with pytest.raises(ValueError, match="仅支持 http/https"):
        S3SyncStorage.upload_from_url(object(), url="file:///etc/passwd")


def test_upload_from_url_rejects_internal_addresses():
    from storage.s3.s3_storage import S3SyncStorage

    with pytest.raises(ValueError, match="拒绝内网|无法解析"):
        S3SyncStorage.upload_from_url(object(), url="http://127.0.0.1:8300/api/memories")


# ── 5. 收藏搜索口径 ────────────────────────────────────────────────
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
