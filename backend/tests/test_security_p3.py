"""TD-P3 安全二线回归测试（2026-08-26 · 对应侦察 S7 中危 M1/M2/M3/M4 + L1/M6）

验收四项：
  1. 大数拒绝：/upload/init file_size>500MB / chunk_size 过小 → 422；get_status 分片数守卫
  2. OTP 作废：同一 phone 连续 ≥5 次错误 → 作废该码 + 冷却（429）
  3. 越权 job 403：job.meta.user_id 归属校验（classify + corrections）+ enqueue 写 meta
  4. cos_key 越权 422：他人前缀 / 对象不存在 → 422 CONTENT_009
附带：job 失败脱敏（不直出 exc_info）、interview/questions 补鉴权、refresh 哈希落库（M6）。

前置：本地 PostgreSQL yishu 库 + Redis（RQ）——与既有认证/上传/内容测试同环境。
"""
import secrets
import uuid

import pytest
from app.db.session import SessionLocal
from app.main import app
from app.services import upload as upload_svc
from app.services.external.storage import get_storage_backend
from fastapi.testclient import TestClient
from sqlalchemy import delete as sa_delete
from sqlalchemy import select


@pytest.fixture()
def client():
    return TestClient(app)


def _phone() -> str:
    """11 位唯一手机号（纯数字匹配 pattern ^1\\d{10}$；避开存量 137/138/139 前缀，也不撞限流）"""
    return f"136{secrets.randbelow(10**8):08d}"


# ---------------------------------------------------------------------------
# M1：/upload/init 大数构造 DoS
# ---------------------------------------------------------------------------


def test_upload_init_oversize_rejected(client, auth_headers):
    """file_size > 500MB → 422 UPLOAD_001（服务层 init_upload 同款拒绝）"""
    _, headers = auth_headers("p3-m1-over")
    r = client.post(
        "/api/v1/upload/init",
        data={
            "client_upload_id": "cid-big",
            "file_name": "big.bin",
            "file_size": 600 * 1024 * 1024,
            "chunk_size": 8 * 1024 * 1024,
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "UPLOAD_001"


def test_upload_init_small_chunk_rejected(client, auth_headers):
    """chunk_size < 下限 → 422 UPLOAD_001（防 chunk_count 爆炸）"""
    _, headers = auth_headers("p3-m1-chunk")
    r = client.post(
        "/api/v1/upload/init",
        data={
            "client_upload_id": "cid-chunk",
            "file_name": "x.bin",
            "file_size": 1024 * 1024,
            "chunk_size": 16,
        },
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "UPLOAD_001"


def test_upload_init_normal_still_works(client, auth_headers, cleanup_user):
    """合法参数不受影响（不误伤正常分片上传）"""
    user_id, headers = auth_headers("p3-m1-ok")
    try:
        r = client.post(
            "/api/v1/upload/init",
            data={
                "client_upload_id": "cid-ok",
                "file_name": "ok.jpg",
                "file_size": 2500,
                "chunk_size": 1024,
            },
            headers=headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["data"]["chunk_count"] == 3  # 2500/1024 → 3
    finally:
        db = SessionLocal()
        try:
            cleanup_user(db, user_id)
        finally:
            db.close()


def test_get_status_guards_huge_chunk_count():
    """get_status 分片数守卫：迁移前遗留的恶意任务行（chunk_count 10^12）不得物化列表"""
    from app.db.models import UploadChunk, UploadTask, User

    db = SessionLocal()
    user_id = None
    task_id = None
    try:
        user = User(phone=f"p3-status-{uuid.uuid4().hex[:8]}", status=1)
        db.add(user)
        db.commit()
        db.refresh(user)
        user_id = user.id
        task = UploadTask(
            id=str(uuid.uuid4()),
            user_id=user.id,
            client_upload_id="cid-huge",
            file_name="x.bin",
            file_size=10**12,
            chunk_size=1,
            chunk_count=10**12,
            file_key="photos/x.bin",
            storage="fake",
            status="pending",
        )
        db.add(task)
        db.commit()
        task_id = task.id
        with pytest.raises(ValueError):
            upload_svc.get_status(db, task.id, user_id=user.id)
    finally:
        if task_id:
            db.execute(sa_delete(UploadChunk).where(UploadChunk.upload_id == task_id))
            db.execute(sa_delete(UploadTask).where(UploadTask.id == task_id))
        if user_id:
            db.execute(sa_delete(UploadTask).where(UploadTask.user_id == user_id))
            db.execute(sa_delete(User).where(User.id == user_id))
        db.commit()
        db.close()


# ---------------------------------------------------------------------------
# M2：OTP 作废 + 冷却
# ---------------------------------------------------------------------------


def test_otp_invalidated_after_5_failures(client):
    """每 phone 每窗口失败 ≥5 次 → 作废该码 + 冷却（第 5 次起 429，正确码也不可用）"""
    from app.db.models import SmsCode

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


def test_otp_below_threshold_still_allows_success(client):
    """失败 <5 次不误伤：4 次错误后正确码仍可登录"""
    from app.db.models import SmsCode, User

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
# M3：RQ job 归属校验 + 脱敏
# ---------------------------------------------------------------------------


class _FakeJob:
    """轻量 RQ Job 桩（避免依赖 Redis 真实 job）"""

    def __init__(self, meta, status="queued", result=None, exc_info=None):
        self.meta = meta
        self._status = status
        self._result = result
        self.exc_info = exc_info

    def get_status(self):
        return self._status

    def return_value(self):
        return self._result


def test_classify_enqueue_writes_user_id_meta(client, auth_headers, monkeypatch):
    """enqueue 时 job.meta 写入 user_id（归属校验的数据来源）"""
    user_id, headers = auth_headers("p3-job-meta")
    captured: dict = {}

    class _FakeOut:
        id = "job-meta-1"

    def fake_enqueue(func, *args, **kwargs):
        captured["meta"] = kwargs.get("meta")
        return _FakeOut()

    monkeypatch.setattr("app.api.classify.enqueue_high", fake_enqueue)
    r = client.post("/api/v1/classify", json={"text": "明天记得买牛奶"}, headers=headers)
    assert r.status_code == 200, r.text
    assert captured["meta"] == {"user_id": user_id}


def test_classify_job_cross_user_403(client, auth_headers, monkeypatch):
    """用户 B 查询用户 A 的 job → 403 CLASSIFY_003（越权轮询他人分类结果）"""
    user_a, _ = auth_headers("p3-job-a")
    _, headers_b = auth_headers("p3-job-b")

    monkeypatch.setattr(
        "app.api.classify.get_job", lambda job_id: _FakeJob({"user_id": user_a})
    )
    r = client.get("/api/v1/classify/jobs/secret-job-id", headers=headers_b)
    assert r.status_code == 403
    assert r.json()["code"] == "CLASSIFY_003"


def test_classify_job_owner_can_query(client, auth_headers, monkeypatch):
    """归属一致（owner 查询自己 job）不误伤"""
    user_id, headers = auth_headers("p3-job-own")
    monkeypatch.setattr(
        "app.api.classify.get_job",
        lambda job_id: _FakeJob(
            {"user_id": user_id},
            status="finished",
            result={"label": "todo", "label_cn": "待办", "confidence": 0.9, "scores": []},
        ),
    )
    r = client.get("/api/v1/classify/jobs/own-job-id", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["status"] == "finished"


def test_classify_job_failure_error_sanitized(client, auth_headers, monkeypatch):
    """失败仅回传脱敏错误（不再直出 exc_info 末行，防内部路径/堆栈泄漏）"""
    user_id, headers = auth_headers("p3-job-fail")
    raw_exc = (
        "Traceback (most recent call last):\n"
        '  File "/srv/app/backend/app/services/secret_path.py", line 42, in foo\n'
        "ValueError: boom"
    )
    monkeypatch.setattr(
        "app.api.classify.get_job",
        lambda job_id: _FakeJob({"user_id": user_id}, status="failed", exc_info=raw_exc),
    )
    r = client.get("/api/v1/classify/jobs/fail-job-id", headers=headers)
    assert r.status_code == 200
    err = r.json()["data"]["error"]
    assert err == "任务执行失败，请稍后重试"
    assert "secret_path" not in err and "Traceback" not in err


def test_arbitrate_job_cross_user_403(client, auth_headers, monkeypatch):
    """corrections 三层裁决 job 越权同样 403 CORR_004"""
    user_a, _ = auth_headers("p3-arb-a")
    _, headers_b = auth_headers("p3-arb-b")

    # corrections.arbitrate_job_status 在函数内 `from app.core.queue import get_job`，
    # 需 monkeypatch app.core.queue.get_job
    monkeypatch.setattr(
        "app.core.queue.get_job", lambda job_id: _FakeJob({"user_id": user_a})
    )
    r = client.get("/api/v1/classify/arbitrate/jobs/arb-secret", headers=headers_b)
    assert r.status_code == 403
    assert r.json()["code"] == "CORR_004"


# ---------------------------------------------------------------------------
# M4：create_content cos_key 归属/前缀/存在性校验
# ---------------------------------------------------------------------------


def test_create_content_cos_key_cross_user_rejected(client, auth_headers):
    """A 提交 B 前缀的 cos_key → 422 CONTENT_009（跨租户对象拉取被拒）"""
    _, headers_a = auth_headers("p3-ck-a")
    user_b, _ = auth_headers("p3-ck-b")
    body = {
        "content_type": "voice",
        "cos_key": f"voice/{user_b}/202608/victim.wav",
        "source": "app",
    }
    r = client.post("/api/v1/contents", json=body, headers=headers_a)
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_009"


def test_create_content_cos_key_missing_object_rejected(client, auth_headers):
    """本用户前缀但对象不存在 → 422 CONTENT_009（任意 key 不触发存储遍历/管线）"""
    user_id, headers = auth_headers("p3-ck-miss")
    body = {
        "content_type": "photo",
        "cos_key": f"photos/{user_id}/202608/nonexistent.jpg",
        "source": "app",
    }
    r = client.post("/api/v1/contents", json=body, headers=headers)
    assert r.status_code == 422
    assert r.json()["code"] == "CONTENT_009"


def test_create_content_cos_key_valid_passes(client, auth_headers):
    """本用户前缀 + 对象存在 → 正常入库（不误伤合法 voice/photo 回传）"""
    user_id, headers = auth_headers("p3-ck-ok")
    cos_key = f"voice/{user_id}/202608/ok_{uuid.uuid4().hex[:8]}.wav"
    get_storage_backend().put_object(cos_key, b"fake-wav-bytes")
    body = {
        "content_type": "voice",
        "cos_key": cos_key,
        "source": "app",
        "extra": {"duration_ms": 1000},
    }
    try:
        r = client.post("/api/v1/contents", json=body, headers=headers)
        assert r.status_code == 200, r.text
        assert r.json()["data"]["content_type"] == "voice"
    finally:
        from app.db.models import Content
        from app.db.session import SessionLocal

        db = SessionLocal()
        try:
            db.execute(sa_delete(Content).where(Content.user_id == user_id))
            db.commit()
        finally:
            db.close()


# ---------------------------------------------------------------------------
# L1：interview/questions 补鉴权
# ---------------------------------------------------------------------------


def test_interview_questions_requires_auth(client):
    """未带 token → 401（与全站鉴权约定一致）"""
    r = client.get("/api/v1/interview/questions")
    assert r.status_code == 401


def test_interview_questions_with_auth(client, auth_headers):
    """登录后可正常获取三问（不误伤冷启动访谈流程）"""
    _, headers = auth_headers("p3-interview")
    r = client.get("/api/v1/interview/questions", headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]


# ---------------------------------------------------------------------------
# M6：refresh token 哈希落库（devices 表不再明文）
# ---------------------------------------------------------------------------


def test_refresh_token_stored_as_hash(client, auth_headers):
    """devices.refresh_token 清空、refresh_token_hash 落 HMAC-SHA256（M6/L2 + G1/R6#8）"""
    from app.db.models import Device, User
    from app.services.auth.auth import _hash_refresh_token
    from sqlalchemy import select

    code = f"p3-hash-{uuid.uuid4().hex[:8]}"
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
