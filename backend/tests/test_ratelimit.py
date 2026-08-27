"""通用限流中间件单测（G1/R6#2/#3，认证安全）

独立 mini-app（不依赖 DB/Redis）：仅挂 RateLimitMiddleware + RequestIDMiddleware
（外层）+ 一个 auth 域探针路由，验证：
  - 同 IP 超阈值 → 429（统一信封 + X-Request-ID 保留，request_id 链路不破坏）
  - user 维度超阈值 → 429（Bearer token 可解出 user_id 时叠加）
  - 白名单 IP 放行（超阈值也不 429；trust_proxy 解析 X-Forwarded-For）
  - Redis 降级不 500（store 抛异常 → 自动降级 MemoryStore）
  - 非三域路径不拦截；rate_limit_enabled=False 时全放行

注意：限流中间件在 conftest 默认关闭（防共享 Redis 干扰整仓），本文件
独立 fixture 显式开启（MemoryStore + 小阈值），与全仓测试隔离。
"""
import pytest
from app.core.config import settings
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    """mini-app：RateLimit 内侧 + RequestID 外侧（对齐 main.py 的 request_id 链路）"""
    from app.core.middleware import RequestIDMiddleware
    from app.core.ratelimit import RateLimitMiddleware

    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)
    app.add_middleware(RequestIDMiddleware)

    @app.get("/api/v1/auth/probe")
    def probe():
        return {"ok": True}

    @app.get("/api/v1/other/probe")  # 非三域：应放行
    def other():
        return {"ok": True}

    return app


@pytest.fixture()
def rl(monkeypatch):
    """开启限流 + 注入独立 MemoryStore（小阈值触发 429）"""
    import app.core.ratelimit as rl_mod

    monkeypatch.setattr(settings, "rate_limit_enabled", True)
    monkeypatch.setattr(settings, "rate_limit_whitelist", "")
    monkeypatch.setattr(settings, "rate_limit_trust_proxy", False)
    monkeypatch.setattr(settings, "rate_limit_window", 60)
    monkeypatch.setattr(settings, "rate_limit_auth_ip", 3)
    monkeypatch.setattr(settings, "rate_limit_auth_user", 2)
    rl_mod.set_store(rl_mod.MemoryRateLimitStore())
    return rl_mod


@pytest.fixture()
def client(rl):
    return TestClient(_make_app())


def test_rate_limit_429_by_ip(client):
    """同 IP 超阈值 → 429（RATE_LIMITED 信封 + 保留 X-Request-ID）"""
    for _ in range(3):
        r = client.get("/api/v1/auth/probe")
        assert r.status_code == 200
    r = client.get("/api/v1/auth/probe")
    assert r.status_code == 429
    body = r.json()
    assert body["code"] == "RATE_LIMITED"
    assert body["details"]["dimension"] == "ip"
    # request_id 链路不破坏（RequestID 最外层 → 429 也带 X-Request-ID）
    assert r.headers.get("x-request-id")
    assert body["request_id"] == r.headers["x-request-id"]


def test_rate_limit_by_user_dimension(client):
    """user 维度超阈值 → 429（dimension=user；Bearer token 可解出 user_id 叠加）"""
    from app.core.security import create_access_token

    token = create_access_token("u-1", "d-1")
    headers = {"Authorization": f"Bearer {token}"}
    # user 限额 2 < ip 限额 3：第 3 次触发 user 维度
    for _ in range(2):
        r = client.get("/api/v1/auth/probe", headers=headers)
        assert r.status_code == 200
    r = client.get("/api/v1/auth/probe", headers=headers)
    assert r.status_code == 429
    assert r.json()["details"]["dimension"] == "user"


def test_rate_limit_no_bearer_only_ip_dimension(client):
    """无 Bearer token → 仅 IP 维度限流（user 维度跳过，坏 token 不放大攻击面）"""
    for _ in range(3):
        assert client.get("/api/v1/auth/probe").status_code == 200
    assert client.get("/api/v1/auth/probe").status_code == 429


def test_rate_limit_whitelist_bypass(client, rl, monkeypatch):
    """白名单 IP 放行（trust_proxy 解析 X-Forwarded-For；白名单 IP 超阈值也不 429）"""
    monkeypatch.setattr(settings, "rate_limit_trust_proxy", True)
    monkeypatch.setattr(settings, "rate_limit_whitelist", "1.2.3.4")
    for _ in range(10):
        r = client.get("/api/v1/auth/probe", headers={"X-Forwarded-For": "1.2.3.4"})
        assert r.status_code == 200, r.text
    # 非白名单 IP 仍被限流
    for _ in range(3):
        assert (
            client.get("/api/v1/auth/probe", headers={"X-Forwarded-For": "9.9.9.9"}).status_code
            == 200
        )
    assert (
        client.get("/api/v1/auth/probe", headers={"X-Forwarded-For": "9.9.9.9"}).status_code
        == 429
    )


def test_rate_limit_redis_degrade_not_500(client, rl, monkeypatch):
    """Redis 降级不 500：store 抛异常 → 自动降级 MemoryStore 继续放行/限流"""
    class BrokenStore:
        def allow(self, key, limit, window):
            raise ConnectionError("redis down")

    rl.set_store(BrokenStore())
    for _ in range(3):
        r = client.get("/api/v1/auth/probe")
        assert r.status_code == 200, f"降级不应 500，实际 {r.status_code}"
    # 降级后存储已切换为 MemoryStore（进程内继续限流）
    assert isinstance(rl.get_store(), rl.MemoryRateLimitStore)
    # 降级后仍限流（第 4 次 429——MemoryStore 计数）
    assert client.get("/api/v1/auth/probe").status_code == 429


def test_rate_limit_non_scoped_path_passthrough(client):
    """非三域路径不拦截（/api/v1/other/* 直接放行）"""
    for _ in range(10):
        assert client.get("/api/v1/other/probe").status_code == 200


def test_rate_limit_disabled_passthrough(client, monkeypatch):
    """rate_limit_enabled=False → 全放行（中间件短路，零开销）"""
    monkeypatch.setattr(settings, "rate_limit_enabled", False)
    for _ in range(20):
        assert client.get("/api/v1/auth/probe").status_code == 200


def test_rate_limit_domain_registry():
    """三域登记：auth / asr / guard 前缀归位（guard 并入 ASR 域配额）"""
    from app.core.ratelimit import _scope_for_path

    assert _scope_for_path("/api/v1/auth/wechat") == "auth"
    assert _scope_for_path("/api/v1/asr/transcribe") == "asr"
    assert _scope_for_path("/api/v1/guard/check") == "asr"
    assert _scope_for_path("/api/v1/search") == "search"
    assert _scope_for_path("/api/v1/contents") is None
    assert _scope_for_path("/healthz") is None
