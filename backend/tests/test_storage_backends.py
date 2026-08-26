"""外部存储后端契约测试（R8#4 2026-08-27 · 覆盖盲区：生产 COS/FS 存储路径零覆盖）

侦察：external/storage.py 44%（198 stmts / 111 miss），存量测试只覆盖
FakeStorageBackend（test_upload/test_content_upload/test_techdebt_p0 均走 fake），
COS/FS 真后端分支（上传/删除/签名、落盘、路径安全、异常包装）未测。

本文件：
- FilesystemStorageBackend：tmp_path 落盘契约 + 路径安全（拒绝 ../ 绝对路径/反斜杠）
- CosStorageBackend：mock 客户端依赖注入（不建真连接）→ 成功/失败/异常包装/STS
- _cos_retryable 可重试分类（CosClientError / CosServiceError 4xx vs 5xx）
- get_storage_backend 工厂语义（未知后端 ValueError / fake 单例 / fs 新实例）
"""
from __future__ import annotations

import io

import pytest
from app.core.config import settings
from app.services.external.storage import (
    CosStorageBackend,
    FilesystemStorageBackend,
    StorageError,
    _cos_retryable,
    get_storage_backend,
)

# ---------------------------------------------------------------------------
# FilesystemStorageBackend（tmp_path 落盘契约）
# ---------------------------------------------------------------------------


@pytest.fixture()
def fs_backend(tmp_path):
    return FilesystemStorageBackend(root=str(tmp_path))


def test_fs_round_trip(fs_backend):
    """写入 → 读回 → 存在 → 删除 → 不存在（幂等删除）"""
    fs_backend.put_object("photos/2026/x.jpg", b"jpeg-bytes")
    assert fs_backend.get_object("photos/2026/x.jpg") == b"jpeg-bytes"
    assert fs_backend.object_exists("photos/2026/x.jpg") is True
    fs_backend.delete_object("photos/2026/x.jpg")
    assert fs_backend.object_exists("photos/2026/x.jpg") is False
    fs_backend.delete_object("photos/2026/x.jpg")  # 不存在静默
    with pytest.raises(KeyError):
        fs_backend.get_object("photos/2026/x.jpg")


def test_fs_put_creates_parent_dirs(fs_backend, tmp_path):
    """put 自动建父目录（嵌套 key）"""
    fs_backend.put_object("a/b/c/d.txt", b"deep")
    assert (tmp_path / "a" / "b" / "c" / "d.txt").is_file()


def test_fs_overwrite_is_idempotent(fs_backend):
    """覆盖语义（幂等）：同 key 重复写取最后值"""
    fs_backend.put_object("k.txt", b"v1")
    fs_backend.put_object("k.txt", b"v2")
    assert fs_backend.get_object("k.txt") == b"v2"


@pytest.mark.parametrize(
    "bad_key",
    ["/etc/passwd", "../escape", "a/../b", "a\\b", ""],
    ids=["绝对路径", "上级目录逃逸", "路径段内..", "反斜杠分隔", "空键"],
)
def test_fs_rejects_unsafe_keys(fs_backend, bad_key):
    """路径安全：拒绝绝对路径 / .. / 反斜杠 / 空键（防目录穿越）"""
    with pytest.raises(ValueError):
        fs_backend.put_object(bad_key, b"x")


def test_fs_storage_error_wraps_put_failure(fs_backend, monkeypatch):
    """P0-6 异常统一包装：FS 写失败 → StorageError(FS_PUT_FAILED, retryable=False)"""
    from pathlib import Path

    def boom(self, data):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_bytes", boom)
    with pytest.raises(StorageError) as raised:
        fs_backend.put_object("x.bin", b"data")
    assert raised.value.code == "FS_PUT_FAILED"
    assert raised.value.retryable is False


# ---------------------------------------------------------------------------
# CosStorageBackend（mock 客户端依赖注入，不建真连接/不烧 key）
# ---------------------------------------------------------------------------


class _FakeCosBody:
    """模拟 qcloud_cos get_object 响应体"""

    def __init__(self, data: bytes):
        self._data = data

    def get_raw_stream(self):
        return io.BytesIO(self._data)


class _FakeCosClient:
    """最小 COS 客户端桩：记录调用、可注入异常"""

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []
        self.error: Exception | None = None
        self.sts_creds = None

    def _maybe_raise(self, op: str):
        self.calls.append(op)
        if self.error is not None:
            err, self.error = self.error, None
            raise err

    def put_object(self, Bucket=None, Key=None, Body=None, **kwargs):
        self._maybe_raise("put_object")
        self.objects[Key] = Body if isinstance(Body, bytes) else Body.read()

    def get_object(self, Bucket=None, Key=None, **kwargs):
        self._maybe_raise("get_object")
        if Key not in self.objects:
            raise KeyError(Key)
        return {"Body": _FakeCosBody(self.objects[Key])}

    def delete_object(self, Bucket=None, Key=None, **kwargs):
        self._maybe_raise("delete_object")
        self.objects.pop(Key, None)

    def object_exists(self, Bucket=None, Key=None, **kwargs):
        self._maybe_raise("object_exists")
        return Key in self.objects


@pytest.fixture()
def cos_backend():
    """依赖注入：object.__new__ 绕过 __init__（不建真实 CosS3Client）"""
    backend = object.__new__(CosStorageBackend)
    backend._client = _FakeCosClient()
    backend._bucket = "yishu-test-bucket"
    return backend


def test_cos_round_trip(cos_backend):
    """COS put/get/exists/delete 契约（mock 客户端记录调用 + 回读字节）"""
    cos_backend.put_object("photos/u1/a.jpg", b"cos-bytes")
    assert cos_backend._client.objects["photos/u1/a.jpg"] == b"cos-bytes"
    assert cos_backend.get_object("photos/u1/a.jpg") == b"cos-bytes"
    assert cos_backend.object_exists("photos/u1/a.jpg") is True
    cos_backend.delete_object("photos/u1/a.jpg")
    assert cos_backend.object_exists("photos/u1/a.jpg") is False
    assert "put_object" in cos_backend._client.calls
    assert "get_object" in cos_backend._client.calls


def test_cos_init_requires_credentials(monkeypatch):
    """COS 后端未配置 TENCENT_SECRET_ID/KEY/COS_BUCKET → RuntimeError（防误用）"""
    monkeypatch.setattr(settings, "tencent_secret_id", "")
    monkeypatch.setattr(settings, "tencent_secret_key", "")
    monkeypatch.setattr(settings, "cos_bucket", "")
    with pytest.raises(RuntimeError, match="COS 后端未配置"):
        CosStorageBackend()


def test_cos_get_missing_wrapped(cos_backend):
    """对象不存在 → StorageError(COS_GET_FAILED)（P0-6 统一包装，不裸抛）"""
    with pytest.raises(StorageError) as raised:
        cos_backend.get_object("missing/key")
    assert raised.value.code == "COS_GET_FAILED"


def test_cos_put_network_error_wrapped_retryable(cos_backend):
    """网络类异常（CosClientError）→ StorageError(COS_PUT_FAILED, retryable=True)"""
    from qcloud_cos.cos_exception import CosClientError

    cos_backend._client.error = CosClientError.__new__(CosClientError)
    with pytest.raises(StorageError) as raised:
        cos_backend.put_object("k", b"x")
    assert raised.value.code == "COS_PUT_FAILED"
    assert raised.value.retryable is True


def test_cos_put_4xx_wrapped_not_retryable(cos_backend):
    """服务端 4xx（CosServiceError status=400）→ retryable=False（重试无意义）"""
    from qcloud_cos.cos_exception import CosServiceError

    se = CosServiceError.__new__(CosServiceError)
    se.get_status_code = lambda: 400
    cos_backend._client.error = se
    with pytest.raises(StorageError) as raised:
        cos_backend.put_object("k", b"x")
    assert raised.value.code == "COS_PUT_FAILED"
    assert raised.value.retryable is False


def test_cos_delete_failure_wrapped(cos_backend):
    """删除异常 → StorageError(COS_DELETE_FAILED)"""
    from qcloud_cos.cos_exception import CosClientError

    cos_backend._client.error = CosClientError.__new__(CosClientError)
    with pytest.raises(StorageError) as raised:
        cos_backend.delete_object("k")
    assert raised.value.code == "COS_DELETE_FAILED"


def test_cos_stat_failure_wrapped(cos_backend):
    """exists 异常 → StorageError(COS_STAT_FAILED)"""
    from qcloud_cos.cos_exception import CosClientError

    cos_backend._client.error = CosClientError.__new__(CosClientError)
    with pytest.raises(StorageError) as raised:
        cos_backend.object_exists("k")
    assert raised.value.code == "COS_STAT_FAILED"


def test_cos_get_sts_credentials(cos_backend, monkeypatch):
    """STS 临时凭证：走 Credential.get_credential（region/role_arn 透传）

    注：当前安装版 qcloud_cos 无 `qcloud_cos.sts` 子模块（STS 直传未接线，
    UPLOAD_005/008 登记）——测试经 sys.modules 注入 fake Credential，
    验证 contract：路径级白名单 policy + 参数透传（不依赖上游 SDK 是否实现）。
    """
    import sys
    import types

    class FakeCredential:
        instances: list[dict] = []

        def __init__(self, **kwargs):
            self.policy = kwargs.get("policy")
            FakeCredential.instances.append(kwargs)

        def get_credential(self, region=None, role_arn=None, **kwargs):
            return {"Credentials": {"TmpSecretId": "sts"}, "region": region, "role_arn": role_arn}

    fake_module = types.ModuleType("qcloud_cos.sts")
    fake_module.Credential = FakeCredential
    monkeypatch.setitem(sys.modules, "qcloud_cos.sts", fake_module)
    monkeypatch.setattr(settings, "tencent_sts_role_arn", "qcs::cam::root/sts")

    cred = cos_backend.get_sts_credentials("user-123")
    assert cred["Credentials"]["TmpSecretId"] == "sts"
    assert cred["region"] == settings.cos_region
    assert cred["role_arn"] == "qcs::cam::root/sts"
    # 路径级白名单：只允许当前用户前缀（P0-2，防跨用户覆盖）
    resources = FakeCredential.instances[0]["policy"]["statement"][0]["resource"]
    assert any("photos/user-123/*" in r for r in resources)
    assert not any("/*" in r and "/user-123/" not in r for r in resources)


# ---------------------------------------------------------------------------
# _cos_retryable 可重试分类（P0-6）
# ---------------------------------------------------------------------------


def test_cos_retryable_client_error():
    """CosClientError（网络层）→ 可重试"""
    from qcloud_cos.cos_exception import CosClientError

    assert _cos_retryable(CosClientError.__new__(CosClientError)) is True


def test_cos_retryable_service_error_by_status():
    """CosServiceError：5xx → 可重试；4xx → 不可重试（重试无意义）"""
    from qcloud_cos.cos_exception import CosServiceError

    se500 = CosServiceError.__new__(CosServiceError)
    se500.get_status_code = lambda: 500
    assert _cos_retryable(se500) is True

    se404 = CosServiceError.__new__(CosServiceError)
    se404.get_status_code = lambda: 404
    assert _cos_retryable(se404) is False


def test_cos_retryable_generic_exception():
    """非 COS 异常（缺 SDK 时兜底）→ 可重试（保守重试）"""
    assert _cos_retryable(ConnectionError("timeout")) is True


# ---------------------------------------------------------------------------
# get_storage_backend 工厂语义
# ---------------------------------------------------------------------------


def test_factory_unknown_backend_raises():
    """未知后端名 → ValueError（防配置拼错静默降级）"""
    with pytest.raises(ValueError, match="未知存储后端"):
        get_storage_backend("s3-typo")


def test_factory_fake_returns_shared_singleton():
    """fake 进程级单例（分片跨调用合并依赖同一实例）"""
    assert get_storage_backend("fake") is get_storage_backend("fake")


def test_factory_fs_returns_fresh_instance(tmp_path, monkeypatch):
    """fs 每次新建实例（无外部连接，无需单例）"""
    monkeypatch.setattr(settings, "fs_storage_root", str(tmp_path / "fs-root"))
    b1 = get_storage_backend("fs")
    b2 = get_storage_backend("fs")
    assert isinstance(b1, FilesystemStorageBackend)
    assert b1 is not b2
