"""G2 越权与纵深专项测试（2026-08-27 · R6#11/#15 + R4#7）

覆盖（对齐 docs/重构批次G提示词_20260827.md G2 验收）：
  - 安全响应头：X-Content-Type-Options / X-Frame-Options / Referrer-Policy
    全接口下发（含错误响应）；HSTS 仅生产（app_env=production）下发
  - 生产关闭文档暴露：create_app(app_env=production) 时
    docs_url/openapi_url/redoc_url 全 None → /docs /openapi.json /redoc 全 404
  - /healthz 收敛：只返回 status + 两类运维事实（agent 探活/降级计数），
    不泄露 env/mock 开关/DB/版本明细
  - sync_pull limit 上限：<1 或 >MAX_PULL_LIMIT → 422 SYNC_001（明确错误码）
"""
from app.core.config import settings
from app.main import app, create_app
from fastapi.testclient import TestClient

# 期望全接口下发的安全响应头（G2/R6#11）
SECURITY_HEADERS = {
    "x-content-type-options": "nosniff",
    "x-frame-options": "DENY",
    "referrer-policy": "no-referrer",
}
HSTS = "max-age=31536000; includeSubDomains"
# healthz 严禁出现的关键字（R6#15：最小存活信息，不泄露环境/开关/DB/版本）
HEALTHZ_FORBIDDEN = {
    "env", "mock", "mock_external_ai", "database", "db", "dsn", "connection",
    "connection_string", "version", "sentry", "redis", "qdrant", "secret",
}


# ---------- 安全响应头（R6#11） ----------

def test_security_headers_on_success(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    for header, expected in SECURITY_HEADERS.items():
        assert r.headers.get(header) == expected, f"200 响应缺少安全头 {header}"


def test_security_headers_on_error(client):
    """纵深：错误响应同样带安全头（防经错误路径漏头/嗅探）"""
    r = client.get("/api/v1/contents")  # 未鉴权 → 401
    assert r.status_code == 401
    for header, expected in SECURITY_HEADERS.items():
        assert r.headers.get(header) == expected, f"401 响应缺少安全头 {header}"
    r2 = client.get("/api/v1/sync/pull", params={"device_id": "g2", "limit": 0})
    assert r2.status_code == 401  # 未鉴权先拦，不影响 422 语义（见 limit 用例）
    assert r2.headers.get("x-content-type-options") == "nosniff"


def test_hsts_only_in_production(monkeypatch):
    """HSTS 仅生产下发（开发环境 http 下不应下发，防本地浏览器踩 HSTS 坑）"""
    # 默认（development）app：无 HSTS
    r = TestClient(app).get("/healthz")
    assert r.headers.get("strict-transport-security") is None

    # 生产 create_app：HSTS 头存在
    monkeypatch.setattr(settings, "app_env", "production")
    r2 = TestClient(create_app()).get("/healthz")
    assert r2.status_code == 200
    assert r2.headers.get("strict-transport-security") == HSTS


# ---------- 生产关闭 /docs（R6#11） ----------

def test_docs_open_in_dev():
    """dev（默认）：docs/openapi/redoc 可用（契约即代码，联调需用）"""
    assert app.docs_url == "/docs"
    assert app.openapi_url == "/openapi.json"
    assert app.redoc_url == "/redoc"
    client = TestClient(app)
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200
    assert client.get("/redoc").status_code == 200


def test_docs_closed_in_production(monkeypatch):
    """生产：docs_url/openapi_url/redoc_url 全 None → 文档与契约端点全 404"""
    monkeypatch.setattr(settings, "app_env", "production")
    prod_app = create_app()
    assert prod_app.docs_url is None
    assert prod_app.openapi_url is None
    assert prod_app.redoc_url is None
    client = TestClient(prod_app)
    for path in ("/docs", "/openapi.json", "/redoc"):
        r = client.get(path)
        assert r.status_code == 404, f"生产环境 {path} 不应暴露（got {r.status_code}）"


def test_production_docs_closed_business_routes_alive(monkeypatch):
    """生产关文档不伤业务：核心路径仍存活（healthz 200；auth 401 而非 404）"""
    monkeypatch.setattr(settings, "app_env", "production")
    client = TestClient(create_app())
    assert client.get("/healthz").status_code == 200
    assert client.post("/api/v1/auth/wechat", json={"code": "", "device_id": "g2"}).status_code == 400


# ---------- /healthz 收敛（R6#15） ----------

def test_healthz_minimal(client):
    """healthz 只暴露存活信息 + 若干运维事实，无任何环境/开关/DB/版本明细。

    2026-10-16（台账 §7.12「静默降级显性化」）：新增 `agent`（backend→agent 探活）
    与 `agent_degradation`（agent 侧 W1 镜像 / W2 检索合并的降级计数）两个**运维事实**
    键——用于把「AI 说已记住，App 时间轴却空白」这类静默降级变可见。两者均非敏感，
    且**不含令牌指纹**（指纹只进两侧启动日志）。断言改为"白名单键 + 禁词集"，既守住
    R6#15 的最小化契约，又允许这两个刻意新增的键。

    2026-10-25（D-23 · 4520 条 processing 卡死）：再新增 `stale_processing_count` /
    `stale_processing_degraded` 两个**运维事实**键——用于把"内容卡在 processing 且无人
    处理"这类静默故障变可见（修复前这 4520 条完全不可观测，症状只是"搜不到自己的照片"）。
    结构性计数，不含任何用户内容/连接串/版本，仍守 R6#15 最小化契约。
    """
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    allowed = {
        "status",
        "agent",
        "agent_degradation",
        "agent_service_token_fp",
        "stale_processing_count",
        "stale_processing_degraded",
    }
    assert set(body) <= allowed, f"healthz 出现未预期键: {sorted(set(body) - allowed)}"
    leaked = HEALTHZ_FORBIDDEN & {k.lower() for k in body}
    assert not leaked, f"healthz 泄露内部信息键: {sorted(leaked)}"
    # 降级计数形态固定（agent 不可达时给 {total: 0}，不制造假计数）
    assert isinstance(body.get("agent_degradation"), dict)
    assert "total" in body["agent_degradation"]
    # 中间件链路仍工作（request_id 保留——运维排障可用，非敏感）
    assert r.headers.get("x-request-id")


# ---------- sync_pull limit 上限（R4#7） ----------

def test_sync_pull_limit_over_cap_rejected(client, auth_headers):
    """超上限/非法 limit → 422 SYNC_001（明确错误码，不静默截断）"""
    _, headers = auth_headers("g2")
    for bad in (0, -1, 501, 100000):
        r = client.get(
            "/api/v1/sync/pull",
            params={"device_id": "g2-dev", "since": 0, "limit": bad},
            headers=headers,
        )
        assert r.status_code == 422, f"limit={bad} 应 422: {r.text}"
        assert r.json()["code"] == "SYNC_001", r.text


def test_sync_pull_limit_within_cap_ok(client, auth_headers):
    """上限内合法 limit 正常放行（回归：默认 200 与上限 500 均可拉取）"""
    from app.api.sync import MAX_PULL_LIMIT

    _, headers = auth_headers("g2")
    for ok in (200, MAX_PULL_LIMIT):
        r = client.get(
            "/api/v1/sync/pull",
            params={"device_id": "g2-dev", "since": 0, "limit": ok},
            headers=headers,
        )
        assert r.status_code == 200, f"limit={ok} 应放行: {r.text}"
        assert "changes" in r.json()["data"]
