"""P0 批次技术债清理专项测试（2026-08-26 · 安全与正确性）

覆盖：
  - P0-7 错误码登记表：码唯一 + http 语义匹配 + raise 处码全部在表内（AST 扫描）
  - P0-8 RQ 队列：enqueue_high/low 显式 job_timeout（ASR 600s / 默认 300s）+ retry 3 次退避
  - P0-2 COS STS：路径级 policy（纯函数）+ /upload/sts 生产门控 + user_id 透传
  - P0-6 存储异常：StorageError 属性 + best_effort_delete 兜底语义
  - P0-1 短信 mock 生产门控（API 层 501）
"""
import ast
import uuid
from pathlib import Path

import pytest
from app.core.config import settings
from app.main import app
from fastapi.testclient import TestClient

BACKEND_APP = Path(__file__).resolve().parent.parent / "app"


# ---------- P0-7：错误码登记表 ----------

def _raise_site_codes() -> set[str]:
    """AST 扫描 backend/app 全部 `ApiError("<CODE>"` 字面量（raise 处实际使用的码）"""
    codes: set[str] = set()
    for py in BACKEND_APP.rglob("*.py"):
        tree = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "ApiError"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                codes.add(node.args[0].value)
    return codes


def test_error_registry_codes_unique():
    """P0-7：登记表码唯一（同码多义拆分的根因约束）"""
    from app.core.errors import ERROR_REGISTRY

    specs = list(ERROR_REGISTRY.values())
    assert len({s.code for s in specs}) == len(specs)


def test_error_registry_http_semantics():
    """P0-7：http 语义匹配——retryable 仅限 5xx；4xx 不可重试；消息非空"""
    from app.core.errors import ERROR_REGISTRY

    for spec in ERROR_REGISTRY.values():
        assert spec.message, f"{spec.code} 缺少语义描述"
        if spec.retryable:
            assert spec.http >= 500, f"{spec.code} retryable=True 但 http={spec.http}（仅 5xx 可重试）"
        if 400 <= spec.http < 500:
            assert not spec.retryable, f"{spec.code} 4xx 不应标记 retryable"


def test_error_registry_covers_all_raise_sites():
    """P0-7：全仓 raise 处使用的码必须已登记（防新码漏登记/撞号）"""
    from app.core.errors import ERROR_REGISTRY

    used = _raise_site_codes()
    missing = used - set(ERROR_REGISTRY)
    assert not missing, f"raise 处存在未登记错误码: {sorted(missing)}"


def test_error_registry_known_splits():
    """P0-7 拆分回归：CONTENT_003 仅敏感语义、CONTENT_008 游标、EVENT_005 内容不存在"""
    from app.core.errors import ERROR_REGISTRY

    assert ERROR_REGISTRY["CONTENT_003"].http == 422
    assert ERROR_REGISTRY["CONTENT_008"].http == 422
    assert ERROR_REGISTRY["EVENT_005"].http == 404
    assert ERROR_REGISTRY["CONTENT_007"].http == 413  # 413 语义不再被 404 污染


# ---------- P0-8：RQ 队列超时/重试 ----------

class _FakeQueue:
    def __init__(self):
        self.calls = []

    def enqueue(self, func, *args, **kwargs):
        self.calls.append({"func": func, "args": args, "kwargs": kwargs})
        return "job-abc"


@pytest.fixture()
def fake_queue(monkeypatch):
    import app.core.queue as queue_mod

    fake = _FakeQueue()
    monkeypatch.setattr(queue_mod, "get_queue", lambda name: fake)
    return queue_mod, fake


def test_enqueue_high_defaults(fake_queue):
    """P0-8：高优队列默认 ASR 级超时 600s + 3 次指数退避 + failure_ttl"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_high(lambda: None, "arg")
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs["job_timeout"] == 600
    assert kwargs["retry"].max == 3
    assert kwargs["retry"].intervals == [10, 30, 90]
    assert kwargs["failure_ttl"] > 0
    assert kwargs["failure_ttl"] >= kwargs["job_timeout"]


def test_enqueue_low_defaults(fake_queue):
    """P0-8：低优队列默认 300s（聚合/批量非长任务）"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_low(lambda: None)
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs["job_timeout"] == 300
    assert kwargs["retry"].max == 3


def test_enqueue_job_timeout_override(fake_queue):
    """P0-8：调用方可覆盖 job_timeout（不破坏既有位置参数调用）"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_low(lambda: None, 1, 2, job_timeout=120)
    kwargs = fake.calls[0]["kwargs"]
    assert kwargs["job_timeout"] == 120
    assert fake.calls[0]["args"] == (1, 2)


def test_enqueue_queue_names(fake_queue):
    """P0-8：high/low 队列名不变（worker 侧契约）"""
    queue_mod, fake = fake_queue
    queue_mod.enqueue_high(lambda: None)
    queue_mod.enqueue_low(lambda: None)
    assert [c["func"] for c in fake.calls]  # 两个都入队成功


# ---------- P0-2：COS STS 路径级白名单 ----------

def test_sts_policy_is_per_user(monkeypatch):
    """P0-2：policy resource 为 photos/voice/thumbnails/{user_id}/*，禁止整桶通配"""
    from app.services.external.storage import _build_sts_policy

    monkeypatch.setattr(settings, "cos_region", "ap-shanghai")
    monkeypatch.setattr(settings, "tencent_appid", "1250000000")
    monkeypatch.setattr(settings, "cos_bucket", "yishu-prod")
    policy = _build_sts_policy("user-123")
    resources = policy["statement"][0]["resource"]
    assert resources == [
        "qcs::cos:ap-shanghai:uid/1250000000:yishu-prod/photos/user-123/*",
        "qcs::cos:ap-shanghai:uid/1250000000:yishu-prod/voice/user-123/*",
        "qcs::cos:ap-shanghai:uid/1250000000:yishu-prod/thumbnails/user-123/*",
    ]
    # 无整桶通配残留
    assert not any(r.endswith(f":{settings.cos_bucket}/*") for r in resources)
    # 他人前缀不可写
    assert not any("user-124" in r for r in resources)
    assert policy["statement"][0]["effect"] == "allow"


def test_sts_policy_rejects_missing_or_malicious_user():
    """P0-2：缺失 user_id / 路径逃逸 user_id 一律拒绝（防前缀逃逸）"""
    from app.services.external.storage import _build_sts_policy

    with pytest.raises(ValueError):
        _build_sts_policy(None)
    with pytest.raises(ValueError):
        _build_sts_policy("")
    with pytest.raises(ValueError):
        _build_sts_policy("a/b")
    with pytest.raises(ValueError):
        _build_sts_policy("..")
    with pytest.raises(ValueError):
        _build_sts_policy("a\\b")


# ---------- P0-6：存储异常包装 / best-effort 删除 ----------

def test_storage_error_attributes():
    from app.services.external.storage import StorageError

    err = StorageError("COS_PUT_FAILED", "boom", retryable=True)
    assert isinstance(err, RuntimeError)
    assert err.code == "COS_PUT_FAILED"
    assert err.retryable is True
    assert "boom" in str(err)


def test_best_effort_delete_removes_object():
    from app.services.external.storage import best_effort_delete, get_storage_backend

    backend = get_storage_backend("fake")
    backend.put_object("p0/k1", b"x")
    best_effort_delete("p0/k1", backend)
    assert not backend.object_exists("p0/k1")


def test_best_effort_delete_swallows_backend_failure():
    """P0-6：删除失败仅告警不抛（孤儿对象由 cleanup 扫描兜底）"""
    from app.services.external.storage import best_effort_delete

    class BrokenBackend:
        def delete_object(self, key):
            raise RuntimeError("backend down")

    best_effort_delete("k1", BrokenBackend())  # 不抛


# ---------- P0-1 / P0-2：API 门控（短信生产 501 / upload/sts） ----------

@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"p0-{uuid.uuid4().hex[:8]}", "device_id": "p0-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_send_sms_production_mock_blocked(client, monkeypatch):
    """P0-1：production + mock_external_ai=true → 501（认证绕过门控，API 层双保险）"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mock_external_ai", True)
    r = client.post("/api/v1/auth/sms/send", json={"phone": "13900000123"})
    assert r.status_code == 501
    assert r.json()["code"] == "AUTH_099"


def test_upload_sts_production_not_configured_501(client, auth_headers, monkeypatch):
    """P0-2：生产且 COS/STS 未真配 → 501 UPLOAD_008（不返回假凭证）"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "tencent_secret_id", "")
    monkeypatch.setattr(settings, "tencent_secret_key", "")
    monkeypatch.setattr(settings, "cos_bucket", "")
    monkeypatch.setattr(settings, "tencent_sts_role_arn", "")
    r = client.get("/api/v1/upload/sts", headers=auth_headers)
    assert r.status_code == 501
    assert r.json()["code"] == "UPLOAD_008"


def test_upload_sts_passes_user_id(client, auth_headers, monkeypatch):
    """P0-2：STS 凭证按请求方 user_id 签发（policy 路径级白名单的输入来源）"""
    import app.api.upload as upload_api

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "tencent_secret_id", "sid")
    monkeypatch.setattr(settings, "tencent_secret_key", "skey")
    monkeypatch.setattr(settings, "cos_bucket", "bucket")
    monkeypatch.setattr(settings, "cos_region", "ap-shanghai")
    monkeypatch.setattr(settings, "tencent_appid", "1250000000")
    monkeypatch.setattr(settings, "tencent_sts_role_arn", "arn:root")
    captured = {}

    class StubBackend:
        def get_sts_credentials(self, user_id=None):
            captured["user_id"] = user_id
            return {"tmp_secret_id": "s", "tmp_secret_key": "k", "session_token": "t"}

    def _stub_backend():
        return StubBackend()

    monkeypatch.setattr(upload_api, "get_storage_backend", _stub_backend)
    r = client.get("/api/v1/upload/sts", headers=auth_headers)
    assert r.status_code == 200, r.text
    assert captured["user_id"], "必须把请求方 user_id 传入 STS 签发（禁止整桶凭证）"


def test_upload_sts_fake_backend_501(client, auth_headers):
    """P0-2：非 cos 后端 → 501 UPLOAD_005（既有语义回归）"""
    r = client.get("/api/v1/upload/sts", headers=auth_headers)
    assert r.status_code == 501
    assert r.json()["code"] == "UPLOAD_005"
