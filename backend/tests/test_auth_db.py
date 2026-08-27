"""认证真实 DB 集成测试（AUTH-001/003/005/006 全链路）

前置：本地 PostgreSQL yishu 隔离库（scripts/setup_pg.sql + schema.sql 已执行）
运行：pytest backend/tests/test_auth_db.py -v

R8#1（2026-08-27）：跨运行污染修复
  - 登录 code / 手机号 uuid 化（复用 conftest auth_headers(prefix) 模式）——
    固定 code = 固定 unionid/phone，中断运行的残留行被下次复用（实测 4 连败 flaky）。
  - teardown 迁移 conftest cleanup_user_data（devices/user_wechat_bindings 等 30+
    表全链删）；sms_codes 无 user_id，按本文件 phone 前缀单独清。
"""
import hashlib
import uuid

import pytest
from app.db.models import Device, SmsCode, User
from app.db.session import SessionLocal
from sqlalchemy import delete as sa_delete
from sqlalchemy import select
from sqlalchemy import update as sa_update


def _hash(token: str) -> str:
    """G1/R6#8：devices.refresh_token_hash 现行格式 = HMAC-SHA256+独立密钥（`hmac$` 前缀）。

    直接用服务层 _hash_refresh_token（与实现同源，不再手写 sha256 副本——
    手写副本在哈希算法升级后必然漂移，这里是断言对齐而非独立实现）。
    """
    from app.services.auth.auth import _hash_refresh_token

    return _hash_refresh_token(token)


def _code(prefix: str) -> str:
    """唯一登录 code（uuid 化，仿 conftest auth_headers）：跨运行不撞身份。

    mock 微信登录下 unionid = mock-unionid-{code}，uuid code → 每次全新用户。
    """
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _phone() -> str:
    """唯一 11 位数字手机号（139 + 8 位随机数字，schema ^1\\d{10}$）：
    避免固定号跨运行污染防刷表/验证码表。"""
    digits = str(uuid.uuid4().int)[-8:]  # uuid.hex 含 a-f，手机号必须纯数字
    return f"139{digits}"


@pytest.fixture()
def db(cleanup_user):
    session = SessionLocal()
    yield session
    # R8#1：teardown 改 conftest cleanup_user_data（devices 等 30+ 表按 user 全链删，
    # 不再只按 unionid/phone LIKE 删 user 自身）；sms_codes 无 user_id 单独按 phone 清。
    test_users = session.query(User).filter(
        (User.unionid.like("mock-unionid-itest-%")) | (User.phone.like("139%"))
    ).all()
    for u in test_users:
        cleanup_user(session, u.id)
    session.query(SmsCode).filter(SmsCode.phone.like("139%")).delete(synchronize_session=False)
    session.query(User).filter(
        (User.unionid.like("mock-unionid-itest-%")) | (User.phone.like("139%"))
    ).delete(synchronize_session=False)
    session.commit()
    session.close()


@pytest.mark.integration
def test_wechat_login_creates_user(client, db):
    """微信登录：新 unionid → 自动建用户 + 返回 token（AUTH-001 前置）"""
    code = _code("itest")
    r = client.post("/api/v1/auth/wechat", json={"code": code, "device_id": "itest-dev"})
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]

    # DB 验证：用户已创建 + devices 记录 refresh
    user = db.execute(
        select(User).where(User.unionid == f"mock-unionid-{code}")
    ).scalar_one_or_none()
    assert user is not None, "unionid 用户应已创建"
    devices = db.execute(select(Device).where(Device.user_id == user.id)).scalars().all()
    # R8#1：固定 code 时代，中断运行的残留 device 行（refresh_token_hash=NULL）会被
    # 复用致连败；uuid code → 每次全新用户 → 必须恰好 1 行 device（无跨运行残留）。
    assert len(devices) == 1, "登录应只产生 1 行 device（无跨运行残留）"
    device = devices[0]
    assert device.device_id == "itest-dev"
    # TD-P3 M6：devices 表不再存明文 refresh —— 只存哈希（DB 泄漏不可直接复用 30 天会话）
    assert device.refresh_token is None, "devices 表不应存明文 refresh"
    assert device.refresh_token_hash == _hash(data["refresh_token"])
    assert device.refresh_rotated_at is not None


@pytest.mark.integration
def test_wechat_login_idempotent(client, db):
    """同 unionid 重复登录 → 不重复建用户"""
    code = _code("itest")
    client.post("/api/v1/auth/wechat", json={"code": code, "device_id": "itest-dev"})
    client.post("/api/v1/auth/wechat", json={"code": code, "device_id": "itest-dev"})
    count = db.execute(
        select(User).where(User.unionid == f"mock-unionid-{code}")
    ).scalars().all()
    assert len(count) == 1


@pytest.mark.integration
def test_phone_login_flow(client, db):
    """手机号验证码全链路：发码 → 登录 → DB 校验（AUTH-003）"""
    phone = _phone()
    # 1. 发码
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 200
    code = r.json()["data"]["mock_code"]

    # 2. 错误验证码 → 401
    r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": "000000"})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_003"

    # 3. 正确验证码 → 200 + 用户创建
    r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": code})
    assert r.status_code == 200
    assert r.json()["data"]["access_token"]

    # 4. DB 验证：验证码已用（used_at 非空）
    used = db.execute(
        select(SmsCode).where(SmsCode.phone == phone, SmsCode.used_at.isnot(None))
    ).scalars().all()
    assert len(used) >= 1


@pytest.mark.integration
def test_sms_rate_limit(client, db):
    """验证码 60s 防刷（AUTH-004）"""
    phone = _phone()
    client.post("/api/v1/auth/sms/send", json={"phone": phone})
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 429
    assert r.json()["code"] == "AUTH_004"


@pytest.mark.integration
def test_refresh_rotation_and_revoke(client, db):
    """refresh 轮换 + 吊销（AUTH-005/006）"""
    # 登录拿 token 对
    code = _code("itest")
    r = client.post("/api/v1/auth/wechat", json={"code": code, "device_id": "itest-dev"})
    tokens = r.json()["data"]
    old_refresh = tokens["refresh_token"]

    # 1. 旧 refresh 换新 → 200，且 devices 表更新为新 token
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 200
    new_refresh = r.json()["data"]["refresh_token"]
    assert new_refresh != old_refresh

    user = db.execute(
        select(User).where(User.unionid == f"mock-unionid-{code}")
    ).scalar_one()
    device = db.execute(
        select(Device).where(Device.user_id == user.id, Device.device_id == "itest-dev")
    ).scalar_one()
    # TD-P3 M6：哈希落库（明文列清空），轮换后新 token 哈希更新
    assert device.refresh_token is None
    assert device.refresh_token_hash == _hash(new_refresh)

    # 2. 旧 refresh 再换 → 401（轮换后旧 token 失效）
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"


@pytest.mark.integration
def test_invalid_refresh_rejected(client):
    """伪造 refresh → 401"""
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": "forged.token.value"})
    assert r.status_code == 401


@pytest.mark.integration
def test_logout_revokes_refresh(client, db):
    """G1/R6#7：logout 吊销该 refresh 绑定的设备会话（AUTH-006）——登出后旧 refresh 不可换新"""
    code = _code("itest")
    r = client.post("/api/v1/auth/wechat", json={"code": code, "device_id": "itest-dev"})
    assert r.status_code == 200
    refresh = r.json()["data"]["refresh_token"]

    # logout → 200 + devices 行 refresh 哈希清空（吊销）
    r = client.post("/api/v1/auth/logout", json={"refresh_token": refresh})
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True

    user = db.execute(
        select(User).where(User.unionid == f"mock-unionid-{code}")
    ).scalar_one()
    device = db.execute(
        select(Device).where(Device.user_id == user.id, Device.device_id == "itest-dev")
    ).scalar_one()
    assert device.refresh_token_hash is None, "logout 后 devices 行 refresh 哈希应清空"
    assert device.refresh_token is None

    # 登出后旧 refresh 换新 → 401 已吊销
    r = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"


@pytest.mark.integration
def test_logout_idempotent_invalid_token(client):
    """G1/R6#7：logout 幂等——伪造/过期 token 仍 200（客户端必清本地凭据）"""
    r = client.post("/api/v1/auth/logout", json={"refresh_token": "forged.token.value"})
    assert r.status_code == 200
    assert r.json()["data"]["ok"] is True


@pytest.mark.integration
def test_sms_code_stored_salted(client, db):
    """G1/R6#9：验证码落库为 SHA-256+盐 哈希（非明文、非裸 sha256、含随机盐）"""
    phone = _phone()
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 200
    code = r.json()["data"]["mock_code"]

    row = db.execute(
        select(SmsCode).where(SmsCode.phone == phone).order_by(SmsCode.id.desc())
    ).scalar_one()
    assert row.salt is not None and len(row.salt) >= 8, "应落库每码随机盐"
    assert row.code != code, "不应存明文验证码"
    assert row.code != hashlib.sha256(code.encode("utf-8")).hexdigest(), "不应存裸 sha256（须加盐）"
    # 加盐哈希 = sha256(f"{salt}:{code}")
    assert row.code == hashlib.sha256(f"{row.salt}:{code}".encode()).hexdigest()


@pytest.mark.integration
def test_refresh_rotation_atomic_single_use(db):
    """R2#7 竞态修复：refresh 轮换条件 UPDATE 原子 single-use

    两会话同时读到同一旧 token 的设备行（校验均通过），随后各自条件 UPDATE：
    先提交者 rowcount=1 轮换成功；后者 WHERE 旧 token 已不命中 → rowcount=0 → 401。
    """
    from app.api.auth import _hash_refresh_token, _rotate_refresh_token
    from app.core.errors import ApiError
    from app.core.security import create_refresh_token

    user = User(phone=_phone(), status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    old_refresh = create_refresh_token(user.id, "race-dev")
    dev = Device(
        user_id=user.id, device_id="race-dev", platform="android",
        refresh_token_hash=_hash_refresh_token(old_refresh), refresh_token=None,
    )
    db.add(dev)
    db.commit()

    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        d1 = s1.execute(
            select(Device).where(Device.user_id == user.id, Device.device_id == "race-dev")
        ).scalar_one()
        d2 = s2.execute(
            select(Device).where(Device.user_id == user.id, Device.device_id == "race-dev")
        ).scalar_one()
        # 两请求都读到旧 token（校验均通过）
        assert d1.refresh_token_hash == _hash_refresh_token(old_refresh)
        assert d2.refresh_token_hash == _hash_refresh_token(old_refresh)

        # 请求1 轮换成功
        t1 = _rotate_refresh_token(s1, user, d1, "race-dev", old_refresh)
        assert t1.refresh_token != old_refresh

        # 请求2 携同一旧 token → WHERE 已不命中 → rowcount=0 → 401 已吊销
        with pytest.raises(ApiError) as ei:
            _rotate_refresh_token(s2, user, d2, "race-dev", old_refresh)
        assert ei.value.http == 401
        assert ei.value.code == "AUTH_005"
    finally:
        s1.close()
        s2.close()
        db.execute(sa_delete(Device).where(Device.user_id == user.id))
        db.execute(sa_delete(User).where(User.phone == user.phone))
        db.commit()

@pytest.mark.integration
def test_phone_code_atomic_consume_single_use(db):
    """R2#8 竞态修复：验证码原子消费——两会话读同一未用码，仅一个 UPDATE 命中

    模拟并发同码双登录：两会话都 SELECT 到同一未用码（校验均通过），随后各自
    UPDATE sms_codes SET used_at=now() WHERE id=:id AND used_at IS NULL——
    先提交者 rowcount=1 消费成功，后者 rowcount=0（该码已被消费 → 401 语义）。
    """
    import hashlib
    from datetime import datetime, timedelta, timezone

    phone = _phone()
    code = "654321"
    sms = SmsCode(
        phone=phone,
        code=hashlib.sha256(code.encode("utf-8")).hexdigest(),
        expire_at=datetime.now(timezone.utc) + timedelta(minutes=5),
    )
    db.add(sms)
    db.commit()
    db.refresh(sms)

    s1 = SessionLocal()
    s2 = SessionLocal()
    try:
        # 两请求都读到未用码（校验均通过）
        r1 = s1.execute(select(SmsCode).where(SmsCode.id == sms.id)).scalar_one()
        r2 = s2.execute(select(SmsCode).where(SmsCode.id == sms.id)).scalar_one()
        assert r1.used_at is None and r2.used_at is None

        now = datetime.now(timezone.utc)
        n1 = s1.execute(
            sa_update(SmsCode)
            .where(SmsCode.id == sms.id, SmsCode.used_at.is_(None))
            .values(used_at=now)
        ).rowcount
        s1.commit()
        # 请求2 原子消费 → 已被请求1置位 → rowcount=0
        n2 = s2.execute(
            sa_update(SmsCode)
            .where(SmsCode.id == sms.id, SmsCode.used_at.is_(None))
            .values(used_at=now)
        ).rowcount
        s2.commit()
        assert n1 == 1 and n2 == 0, "同码并发消费只有第一个命中"
    finally:
        s1.close()
        s2.close()
        db.execute(sa_delete(SmsCode).where(SmsCode.phone == phone))
        db.execute(sa_delete(User).where(User.phone == phone))
        db.commit()


@pytest.mark.integration
def test_send_sms_production_blocked(client, db, monkeypatch):
    """P0-1（审查 H1）：生产环境禁止 mock 验证码直返——任意手机号接管账户的认证绕过

    与 wechat_login 的"生产未接入 → 501"对齐：production + mock_external_ai=true
    也必须 501（mock 验证码仅限非生产；get_settings 启动期已强制生产 mock=False，
    此处双保险验证运行时误配同样拦截）。
    """
    from app.core.config import settings

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mock_external_ai", True)  # 模拟生产误配
    phone = _phone()
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 501
    assert r.json()["code"] == "AUTH_099"
    # 未生成验证码记录（防刷表无残留）
    rows = db.execute(
        select(SmsCode).where(SmsCode.phone == phone)
    ).scalars().all()
    assert rows == []


# ---------------------------------------------------------------------------
# H3：OTP 作废 + 冷却（原 test_security_p3.py M2 按域迁入）
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_otp_invalidated_after_5_failures(client):
    """每 phone 每窗口失败 ≥5 次 → 作废该码 + 冷却（第 5 次起 429，正确码也不可用）"""
    phone = _phone()
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 200, r.text
    code = r.json()["data"]["mock_code"]

    for i in range(5):
        r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": "000000"})
        if i < 4:
            assert r.status_code == 401, f"第 {i + 1} 次应 401，got {r.status_code} {r.text}"
        else:
            assert r.status_code == 429, f"第 5 次应 429 作废，got {r.status_code} {r.text}"
            assert r.json()["code"] == "AUTH_004"

    # 正确验证码也已作废（冷却中 → 429；DB 中该码 used_at 已置位）
    r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": code})
    assert r.status_code == 429
    assert r.json()["code"] == "AUTH_004"

    # 清理验证码记录（防 DB 残留）
    db = SessionLocal()
    try:
        db.execute(sa_delete(SmsCode).where(SmsCode.phone == phone))
        db.commit()
    finally:
        db.close()


@pytest.mark.integration
def test_otp_below_threshold_still_allows_success(client):
    """失败 <5 次不误伤：4 次错误后正确码仍可登录"""
    phone = _phone()
    r = client.post("/api/v1/auth/sms/send", json={"phone": phone})
    assert r.status_code == 200, r.text
    code = r.json()["data"]["mock_code"]

    for _ in range(4):
        r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": "000000"})
        assert r.status_code == 401

    r = client.post("/api/v1/auth/phone", json={"phone": phone, "code": code})
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        from app.db.models import Device, SmsCode, User

        user = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
        if user is not None:
            db.execute(sa_delete(Device).where(Device.user_id == user.id))
        db.execute(sa_delete(SmsCode).where(SmsCode.phone == phone))
        db.execute(sa_delete(User).where(User.phone == phone))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# H3：refresh token 哈希落库（原 test_security_p3.py M6 按域迁入）
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_refresh_token_stored_as_hash(client):
    """devices.refresh_token 清空、refresh_token_hash 落 HMAC-SHA256（M6/L2 + G1/R6#8）"""
    from app.db.models import Device, User
    from app.services.auth.auth import _hash_refresh_token

    code = _code("p3-hash")
    r = client.post(
        "/api/v1/auth/wechat", json={"code": code, "device_id": "p3-hash-dev"}
    )
    assert r.status_code == 200
    refresh = r.json()["data"]["refresh_token"]

    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.unionid == f"mock-unionid-{code}")).scalar_one()
        device = db.execute(
            select(Device).where(Device.user_id == user.id, Device.device_id == "p3-hash-dev")
        ).scalar_one()
        assert device.refresh_token is None, "devices 表不应再存明文 refresh"
        # G1/R6#8：HMAC-SHA256 + 独立密钥（带 `hmac$` 版本前缀），不再裸 sha256
        assert device.refresh_token_hash == _hash_refresh_token(refresh)
        assert device.refresh_token_hash.startswith("hmac$")
        assert device.refresh_rotated_at is not None, "应记录最后轮换时间"

        # 哈希化后 refresh 仍可正常轮换（吊销语义不破坏）
        r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh})
        assert r2.status_code == 200, r2.text
    finally:
        db.execute(sa_delete(Device).where(Device.device_id == "p3-hash-dev"))
        db.execute(sa_delete(User).where(User.unionid == f"mock-unionid-{code}"))
        db.commit()
        db.close()
