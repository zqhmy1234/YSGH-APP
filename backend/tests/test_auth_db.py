"""认证真实 DB 集成测试（AUTH-001/003/005/006 全链路）

前置：本地 PostgreSQL yishu 隔离库（scripts/setup_pg.sql + schema.sql 已执行）
运行：pytest backend/tests/test_auth_db.py -v
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from app.db.models import Device, SmsCode, User
from app.db.session import SessionLocal
from app.main import app
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    # 清理测试数据（先子后父，避免外键冲突；只删本测试产生的记录）
    test_users = session.query(User).filter(
        (User.unionid.like("mock-unionid-itest-%")) | (User.phone.like("139000000%"))
    ).all()
    test_user_ids = [u.id for u in test_users]
    if test_user_ids:
        session.query(Device).filter(Device.user_id.in_(test_user_ids)).delete(synchronize_session=False)
    session.query(SmsCode).filter(SmsCode.phone.like("139000000%")).delete(synchronize_session=False)
    session.query(User).filter(
        (User.unionid.like("mock-unionid-itest-%")) | (User.phone.like("139000000%"))
    ).delete(synchronize_session=False)
    session.commit()
    session.close()


@pytest.mark.integration
def test_wechat_login_creates_user(client, db):
    """微信登录：新 unionid → 自动建用户 + 返回 token（AUTH-001 前置）"""
    r = client.post("/api/v1/auth/wechat", json={"code": "itest-1", "device_id": "itest-dev"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]

    # DB 验证：用户已创建 + devices 记录 refresh
    user = db.execute(select(User).where(User.unionid == "mock-unionid-itest-1")).scalar_one_or_none()
    assert user is not None, "unionid 用户应已创建"
    device = db.execute(
        select(Device).where(Device.user_id == user.id, Device.device_id == "itest-dev")
    ).scalar_one_or_none()
    assert device is not None
    assert device.refresh_token == data["refresh_token"], "devices 表应存 refresh（AUTH-006 吊销依据）"


@pytest.mark.integration
def test_wechat_login_idempotent(client, db):
    """同 unionid 重复登录 → 不重复建用户"""
    client.post("/api/v1/auth/wechat", json={"code": "itest-2", "device_id": "itest-dev"})
    client.post("/api/v1/auth/wechat", json={"code": "itest-2", "device_id": "itest-dev"})
    count = db.execute(
        select(User).where(User.unionid == "mock-unionid-itest-2")
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.integration
def test_phone_login_flow(client, db):
    """手机号验证码全链路：发码 → 登录 → DB 校验（AUTH-003）"""
    # 1. 发码
    r = client.post("/api/v1/auth/sms/send", json={"phone": "13900000000"})
    assert r.status_code == 200
    code = r.json()["data"]["mock_code"]

    # 2. 错误验证码 → 401
    r = client.post("/api/v1/auth/phone", json={"phone": "13900000000", "code": "000000"})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_003"

    # 3. 正确验证码 → 200 + 用户创建
    r = client.post("/api/v1/auth/phone", json={"phone": "13900000000", "code": code})
    assert r.status_code == 200
    assert r.json()["data"]["access_token"]

    # 4. DB 验证：验证码已用（used_at 非空）
    used = db.execute(
        select(SmsCode).where(SmsCode.phone == "13900000000", SmsCode.used_at.isnot(None))
    ).scalars().all()
    assert len(used) >= 1


@pytest.mark.integration
def test_sms_rate_limit(client):
    """验证码 60s 防刷（AUTH-004）"""
    client.post("/api/v1/auth/sms/send", json={"phone": "13900000001"})
    r = client.post("/api/v1/auth/sms/send", json={"phone": "13900000001"})
    assert r.status_code == 429
    assert r.json()["code"] == "AUTH_004"


@pytest.mark.integration
def test_refresh_rotation_and_revoke(client, db):
    """refresh 轮换 + 吊销（AUTH-005/006）"""
    # 登录拿 token 对
    r = client.post("/api/v1/auth/wechat", json={"code": "itest-3", "device_id": "itest-dev"})
    tokens = r.json()["data"]
    old_refresh = tokens["refresh_token"]

    # 1. 旧 refresh 换新 → 200，且 devices 表更新为新 token
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    new_refresh = r.json()["data"]["refresh_token"]
    assert new_refresh != old_refresh

    user = db.execute(select(User).where(User.unionid == "mock-unionid-itest-3")).scalar_one()
    device = db.execute(
        select(Device).where(Device.user_id == user.id, Device.device_id == "itest-dev")
    ).scalar_one()
    assert device.refresh_token == new_refresh

    # 2. 旧 refresh 再换 → 401（轮换后旧 token 失效）
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"


@pytest.mark.integration
def test_invalid_refresh_rejected(client):
    """伪造 refresh → 401"""
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "forged.token.value"})
    assert r.status_code == 401
