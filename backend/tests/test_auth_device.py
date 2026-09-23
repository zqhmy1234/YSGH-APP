"""设备码登录通道测试（内测期临时通道 · 2026-09-21 · 卡 S2）

契约冻结件：`docs/内测下发并行任务卡_20260921.md` §一
  POST /api/v1/auth/device → ApiResponse[TokenPair]（与 /wechat 完全同构）
  unionid = "device:<device_id>"；门控 ALLOW_DEVICE_LOGIN 默认 False → 501 AUTH_014

覆盖：
  1. fail-closed 默认值（不读 .env，纯字段默认）+ 关闭时 501 AUTH_014
  2. 开启后建号发令牌：TokenPair 结构 / unionid 映射 / devices 只存哈希
  3. 同 device_id 幂等：同 user.id 且 devices 不重复
  4. 异 device_id 严格隔离：不同 user.id + token 归属自证（GET /users/me）
  5. 参数边界：全空白（业务 400）vs 超长/缺字段（pydantic 422）
  6. 门控顺序：关闭时不因参数为空白而变成 400 —— 先门控后业务校验
  7. refresh 兼容：本通道签发的 refresh 可正常轮换（不新增第二套会话链）

隔离与清理：设备 id 统一 `itest-dev-<uuid>` 前缀 → unionid `device:itest-dev-*`，
teardown 走 conftest `cleanup_user`（30+ 表全链删），避免跨运行残留（R8#1 教训）。
"""
import uuid

import pytest
from app.db.models import Device, User
from app.db.session import SessionLocal
from sqlalchemy import select

pytestmark = pytest.mark.integration

DEVICE_PREFIX = "itest-dev-"


def _device() -> str:
    """唯一设备 id（跨运行不撞身份；前缀用于 teardown 精确匹配本文件造的数据）"""
    return f"{DEVICE_PREFIX}{uuid.uuid4().hex[:10]}"


def _enable(monkeypatch) -> None:
    """开启内测设备码通道（真实部署由 .env 的 ALLOW_DEVICE_LOGIN=true 控制）"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "allow_device_login", True)


def _disable(monkeypatch) -> None:
    from app.core.config import settings

    monkeypatch.setattr(settings, "allow_device_login", False)


@pytest.fixture()
def db(cleanup_user):
    """DB 会话 + 本文件命名空间的自动清理（unionid LIKE 'device:itest-dev-%'）"""
    session = SessionLocal()
    yield session
    test_users = session.query(User).filter(User.unionid.like(f"device:{DEVICE_PREFIX}%")).all()
    for u in test_users:
        cleanup_user(session, u.id)
    session.query(User).filter(User.unionid.like(f"device:{DEVICE_PREFIX}%")).delete(
        synchronize_session=False
    )
    session.commit()
    session.close()


@pytest.mark.unit
def test_allow_device_login_default_is_fail_closed():
    """门控默认值必须是 False（不看 .env）——内测通道若默认放开＝永久留一条任意设备自助注册后门。

    直接断言 pydantic 字段默认值，而非断言运行时的 settings：本地 .env 可能已置 true（内测期正常），
    「默认关闭」这条纪律必须由**字段定义**保证，不能靠部署时的人为记得。
    """
    from app.core.config import Settings

    assert Settings.model_fields["allow_device_login"].default is False


def test_device_login_gate_closed_returns_501(client, monkeypatch):
    """关闭态：合法请求也一律 501 AUTH_014（不静默降级为 mock/游客）"""
    _disable(monkeypatch)
    r = client.post("/api/v1/auth/device", json={"device_id": _device()})
    assert r.status_code == 501
    body = r.json()
    assert body["code"] == "AUTH_014"
    assert body["request_id"]  # 错误信封完整性（API-008 链路串联）


def test_device_login_gate_checked_before_validation(client, monkeypatch):
    """门控先于业务校验：关闭 + 全空白 device_id → 501（而不是 400）

    两态可区分是刻意的——未启用时不泄露通道的参数校验差异。
    （注意：超长/缺字段仍会是 422，那是 FastAPI 在进 handler 前就拦下的框架校验，与本条不冲突。）
    """
    _disable(monkeypatch)
    r = client.post("/api/v1/auth/device", json={"device_id": "   "})
    assert r.status_code == 501
    assert r.json()["code"] == "AUTH_014"


def test_device_login_creates_user_and_issues_tokens(client, db, monkeypatch):
    """开启态：首次登录建档 + 发令牌 + unionid 映射正确 + devices 只存哈希（不存明文）"""
    from app.services.auth.auth import _hash_refresh_token
    from app.services.auth.providers import DEVICE_UNIONID_PREFIX

    _enable(monkeypatch)
    device_id = _device()
    r = client.post(
        "/api/v1/auth/device",
        json={"device_id": device_id, "platform": "android", "app_version": "0.1.0"},
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]

    # 契约同构：与 /auth/wechat 完全一致的 TokenPair 结构（客户端零分支解析）
    assert data["access_token"] and data["refresh_token"]
    assert data["access_expires_in"] == 7200
    assert data["refresh_expires_in"] == 2592000
    assert data["user"]["id"]

    # 身份映射：unionid = "device:<device_id>"（伪身份借 unionid 列承载，见 providers.py 注释）
    user = db.execute(
        select(User).where(User.unionid == f"{DEVICE_UNIONID_PREFIX}{device_id}")
    ).scalar_one_or_none()
    assert user is not None, "设备用户应已建档"
    assert user.id == data["user"]["id"]

    devices = db.execute(select(Device).where(Device.user_id == user.id)).scalars().all()
    assert len(devices) == 1, "登录应只产生 1 行 device"
    assert devices[0].device_id == device_id
    assert devices[0].platform == "android"
    # TD-P3 M6：devices 表只存 refresh 哈希（DB 泄漏不可直接复用 30 天会话）
    assert devices[0].refresh_token is None
    assert devices[0].refresh_token_hash == _hash_refresh_token(data["refresh_token"])


def test_device_login_idempotent_same_device(client, db, monkeypatch):
    """同 device_id 重复登录 → 同一用户、devices 不重复增长（自助建档不能每次新建号）"""
    _enable(monkeypatch)
    device_id = _device()

    r1 = client.post("/api/v1/auth/device", json={"device_id": device_id})
    r2 = client.post("/api/v1/auth/device", json={"device_id": device_id})
    assert r1.status_code == 200 and r2.status_code == 200
    assert r1.json()["data"]["user"]["id"] == r2.json()["data"]["user"]["id"]

    user = db.execute(select(User).where(User.unionid == f"device:{device_id}")).scalar_one()
    devices = db.execute(select(Device).where(Device.user_id == user.id)).scalars().all()
    assert len(devices) == 1, "重复登录不得重复建 device 行"


def test_device_login_isolated_between_devices(client, monkeypatch):
    """异 device_id 严格隔离：各自独立用户，且 token 只能看到自己（20 人内测的核心正确性）"""
    _enable(monkeypatch)
    dev_a, dev_b = _device(), _device()

    ra = client.post("/api/v1/auth/device", json={"device_id": dev_a})
    rb = client.post("/api/v1/auth/device", json={"device_id": dev_b})
    assert ra.status_code == 200 and rb.status_code == 200
    ida = ra.json()["data"]["user"]["id"]
    idb = rb.json()["data"]["user"]["id"]
    assert ida != idb, "不同设备必须映射到不同用户（否则 20 人数据混在一起）"

    # token 归属自证：用 A 的 access_token 调 /users/me，必须回 A 自己
    ha = {"Authorization": f"Bearer {ra.json()['data']['access_token']}"}
    hb = {"Authorization": f"Bearer {rb.json()['data']['access_token']}"}
    me_a = client.get("/api/v1/users/me", headers=ha)
    me_b = client.get("/api/v1/users/me", headers=hb)
    assert me_a.status_code == 200 and me_b.status_code == 200
    assert me_a.json()["data"]["id"] == ida
    assert me_b.json()["data"]["id"] == idb


def test_device_login_blank_device_id_rejected(client, monkeypatch):
    """开启态：全空白 device_id → 400 AUTH_001（pydantic min_length=1 拦不住空白串）"""
    _enable(monkeypatch)
    r = client.post("/api/v1/auth/device", json={"device_id": "   "})
    assert r.status_code == 400
    assert r.json()["code"] == "AUTH_001"


def test_device_login_device_id_bounds(client, monkeypatch):
    """开启态：超长 → 422；缺字段 → 422（框架层校验，与 WechatLoginRequest 对齐的 64 上限）"""
    _enable(monkeypatch)
    too_long = client.post("/api/v1/auth/device", json={"device_id": "d" * 65})
    assert too_long.status_code == 422
    missing = client.post("/api/v1/auth/device", json={})
    assert missing.status_code == 422


def test_device_login_refresh_roundtrip(client, monkeypatch):
    """兼容性：本通道签发的 refresh 可直接走既有 /auth/refresh 轮换（不新增第二套会话链）"""
    _enable(monkeypatch)
    r = client.post("/api/v1/auth/device", json={"device_id": _device()})
    assert r.status_code == 200
    refresh_token = r.json()["data"]["refresh_token"]

    r2 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["access_token"]
    # AUTH-005：旧 refresh 轮换后立即失效（single-use），旧 token 重放必须 401
    r3 = client.post("/api/v1/auth/refresh", json={"refresh_token": refresh_token})
    assert r3.status_code == 401
    assert r3.json()["code"] == "AUTH_005"
