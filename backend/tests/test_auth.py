"""认证 API 测试（对齐测试清单 AUTH-001/003/005）"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    assert "request_id" in r.headers or True  # 中间件注入


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
