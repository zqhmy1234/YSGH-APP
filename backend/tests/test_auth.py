"""认证 API 测试（对齐测试清单 AUTH-001/003/005）

Wave4-L（M3 微信域）新增：code2session 真实接入（mock 微信响应）——
配置 WECHAT_APPID/SECRET 后走真实 jscode2session；未配置保持 mock/501 语义。
"""
import httpx
import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


def test_healthz(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert r.headers.get("x-request-id")  # 中间件注入（审查修复：删除恒真 or True）


def test_wechat_login_success(client):
    """微信登录 mock 链路：code → token 对（AUTH-001 前置）"""
    r = client.post("/api/v1/auth/wechat", json={"code": "test-abc", "device_id": "dev-001"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]
    assert data["access_expires_in"] == 7200
    assert data["user"]["id"]


def test_wechat_login_empty_code(client):
    """code 为空 → 业务错误 AUTH_001"""
    r = client.post("/api/v1/auth/wechat", json={"code": "", "device_id": "dev-001"})
    assert r.status_code == 400
    assert r.json()["code"] == "AUTH_001"


def test_phone_login_wrong_code(client):
    """手机号登录错误验证码 → 401（AUTH-003）"""
    import time

    phone = f"137{int(time.time()) % 100000000:08d}"
    r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": "111111"})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_003"


def test_sms_send_mock(client):
    """短信发送 mock：返回 6 位随机验证码（真实入库，联调用）"""
    import time

    phone = f"138{int(time.time()) % 100000000:08d}"  # 随机号码，避免 60s 防刷窗口冲突
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 200
    code = r.json()["data"]["mock_code"]
    assert len(code) == 6 and code.isdigit()


def test_refresh_invalid_token(client):
    """无效 refresh → 401（AUTH-005）"""
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "invalid.token.here"})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"


def test_phone_invalid_format(client):
    """手机号格式校验（pattern 1\\d{10}）"""
    r = client.post("/api/v1/auth/phone", json={"phone": "123", "code": "000000"})
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Wave4-L：code2session 真实接入（mock 微信响应；未配置保持 mock/501 语义）
# ---------------------------------------------------------------------------


class _WxResp:
    def __init__(self, payload: dict, status_code: int = 200):
        self.payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self.payload


def _enable_wechat(monkeypatch):
    """配置微信开放平台 appid/secret → 触发真实 code2session 分支"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "wechat_appid", "wx-test-appid")
    monkeypatch.setattr(settings, "wechat_secret", "wx-test-secret")


def test_wechat_login_real_code2session(client, monkeypatch):
    """配置 appid/secret 后：code → jscode2session → unionid 登录（AUTH-001）"""
    _enable_wechat(monkeypatch)
    captured: dict = {}

    def fake_get(url, **kwargs):
        captured["url"] = url
        captured["params"] = kwargs.get("params")
        return _WxResp({"openid": "o-wx-1", "session_key": "sk", "unionid": "u-wx-1"})

    monkeypatch.setattr("httpx.get", fake_get)
    r = client.post("/api/v1/auth/wechat", json={"code": "code-1", "device_id": "dev-1"})
    assert r.status_code == 200
    assert captured["url"] == "https://api.weixin.qq.com/sns/jscode2session"
    assert captured["params"]["appid"] == "wx-test-appid"
    assert captured["params"]["secret"] == "wx-test-secret"
    assert captured["params"]["js_code"] == "code-1"
    assert captured["params"]["grant_type"] == "authorization_code"
    assert r.json()["data"]["user"]["id"]


def test_wechat_login_fallback_openid(client, monkeypatch):
    """未绑定微信开放平台（无 unionid）→ 回退 openid 作稳定主键（同 code 同用户）"""
    _enable_wechat(monkeypatch)
    monkeypatch.setattr(
        "httpx.get", lambda *a, **k: _WxResp({"openid": "o-wx-only", "session_key": "sk"})
    )
    r1 = client.post("/api/v1/auth/wechat", json={"code": "code-2", "device_id": "dev-2"})
    assert r1.status_code == 200
    r2 = client.post("/api/v1/auth/wechat", json={"code": "code-2", "device_id": "dev-2b"})
    assert r2.status_code == 200
    assert r2.json()["data"]["user"]["id"] == r1.json()["data"]["user"]["id"]


def test_wechat_code2session_errcode_rejected(client, monkeypatch):
    """微信业务错误（errcode≠0，code 失效）→ 401 AUTH_001，不降级 mock"""
    _enable_wechat(monkeypatch)
    monkeypatch.setattr(
        "httpx.get",
        lambda *a, **k: _WxResp({"errcode": 40029, "errmsg": "invalid code"}),
    )
    r = client.post("/api/v1/auth/wechat", json={"code": "bad", "device_id": "dev-3"})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_001"


def test_wechat_code2session_upstream_error(client, monkeypatch):
    """微信服务不可用（网络异常）→ 502 AUTH_012（R4#11：AUTH_099 拆分——上游不可用），不静默降级 mock"""
    _enable_wechat(monkeypatch)

    def fake_get(url, **kwargs):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr("httpx.get", fake_get)
    r = client.post("/api/v1/auth/wechat", json={"code": "code-3", "device_id": "dev-4"})
    assert r.status_code == 502
    assert r.json()["code"] == "AUTH_012"


def test_wechat_login_production_not_configured_501(client, monkeypatch):
    """生产环境未配置微信 → 501 AUTH_011（R4#11：AUTH_099 拆分——微信未接入），禁止 mock 登录"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "wechat_appid", "")
    monkeypatch.setattr(settings, "wechat_secret", "")
    monkeypatch.setattr(settings, "app_env", "production")
    r = client.post("/api/v1/auth/wechat", json={"code": "whatever", "device_id": "dev-p"})
    assert r.status_code == 501
    assert r.json()["code"] == "AUTH_011"


# ---------------------------------------------------------------------------
# H3：短信生产门控（原 test_techdebt_p0.py P0-1 按域迁入）
# ---------------------------------------------------------------------------


def test_send_sms_production_mock_blocked(client, monkeypatch):
    """P0-1：production + mock_external_ai=true → 501（认证绕过门控，API 层双保险）"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mock_external_ai", True)
    r = client.post("/api/v1/auth/sms/send", json={"phone": "13900000123"})
    assert r.status_code == 501
    assert r.json()["code"] == "AUTH_099"
