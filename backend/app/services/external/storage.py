"""对象存储抽象层（S5-03 COS 分片/断电续传 · WP-C）

统一接口，三种实现：
  - FakeStorageBackend：内存字典（单测/默认，零依赖）
  - MinioStorageBackend：MinIO（本地模拟断点续传，S3 兼容，docker 起 minio/minio）
  - CosStorageBackend：腾讯云 COS（生产，cos-python-sdk-v5）

配置：settings.storage_backend ∈ {fake, fs, minio, cos}（默认 fake）
MinIO 连接参数：settings.minio_endpoint / minio_access_key / minio_secret_key / minio_bucket
COS 连接参数：复用 TENCENT_SECRET_ID/SECRET_KEY + COS_BUCKET/COS_REGION（config.py 别名读取已对齐）

P0-6（审查 H-3）：外部存储异常统一包装为 StorageError(code, retryable)，
调用方据此分类映射错误码；写对象后 DB commit 失败用 best_effort_delete 兜底。
P0-2（审查 H2）：STS 凭证按用户前缀签发（见 _build_sts_policy），禁止整桶通配。
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from pathlib import Path

from app.core.config import settings

logger = logging.getLogger("yishu.storage")


class StorageError(RuntimeError):
    """对象存储统一异常（P0-6 · 审查 H-3）：外部存储故障不再裸抛 500

    仿 AsrError 模式：code 机器可读；retryable 表示网络/5xx 类可重试错误。
    调用方（API 层/管线）按 code 分类映射错误码，避免无码 500。
    """

    def __init__(self, code: str, message: str, retryable: bool = False):
        self.code = code
        self.retryable = retryable
        super().__init__(message)


def best_effort_delete(key: str, backend=None) -> None:
    """尽力删除对象（P0-6）：写对象后 DB commit 失败的调用点兜底，防孤儿对象

    删除失败仅记日志（孤儿对象由 cleanup_job 的孤儿扫描登记项兜底，见
    workers/cleanup_job.py 头注）。
    """
    try:
        (backend or get_storage_backend()).delete_object(key)
    except Exception:  # noqa: BLE001 —— 尽力而为，不阻断主流程
        logger.warning("best-effort 删除失败（孤儿对象待 cleanup 扫描）key=%s", key)


class StorageBackend(ABC):
    """对象存储统一接口（key 为 COS/S3 对象键）"""

    @abstractmethod
    def put_object(self, key: str, data: bytes) -> None:
        """写入对象（覆盖语义，幂等）"""

    @abstractmethod
    def get_object(self, key: str) -> bytes:
        """读取对象，不存在抛 KeyError"""

    @abstractmethod
    def delete_object(self, key: str) -> None:
        """删除对象（不存在静默）"""

    @abstractmethod
    def object_exists(self, key: str) -> bool:
        """对象是否存在"""

    def get_sts_credentials(self, user_id: str | None = None) -> dict:
        """客户端直传临时凭证；不支持的实现抛 NotImplementedError

        P0-2（审查 H2）：user_id 必传——policy 按用户前缀签发，禁止整桶通配。
        """
        raise NotImplementedError("该存储后端不支持 STS 临时凭证")


class FakeStorageBackend(StorageBackend):
    """内存实现（测试/默认）"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}

    def put_object(self, key: str, data: bytes) -> None:
        self._store[key] = data

    def get_object(self, key: str) -> bytes:
        if key not in self._store:
            raise KeyError(f"object not found: {key}")
        return self._store[key]

    def delete_object(self, key: str) -> None:
        self._store.pop(key, None)

    def object_exists(self, key: str) -> bool:
        return key in self._store


class MinioStorageBackend(StorageBackend):
    """MinIO（本地模拟；S3 兼容）"""

    def __init__(self) -> None:
        from minio import Minio

        self._client = Minio(
            settings.minio_endpoint,
            access_key=settings.minio_access_key,
            secret_key=settings.minio_secret_key,
            secure=False,
        )
        self._bucket = settings.minio_bucket
        if not self._client.bucket_exists(self._bucket):
            self._client.make_bucket(self._bucket)

    def put_object(self, key: str, data: bytes) -> None:
        from minio import S3Error

        try:
            self._client.put_object(
                self._bucket, key, __import__("io").BytesIO(data), len(data)
            )
        except S3Error as exc:
            raise StorageError(
                "MINIO_PUT_FAILED", f"minio put_object 失败: {exc}", retryable=True
            ) from exc

    def get_object(self, key: str) -> bytes:
        from minio import S3Error

        try:
            resp = self._client.get_object(self._bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()
        except S3Error as exc:
            raise KeyError(f"minio object not found: {key}") from exc
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "MINIO_GET_FAILED", f"minio get_object 失败: {type(exc).__name__}", retryable=True
            ) from exc

    def delete_object(self, key: str) -> None:
        try:
            self._client.remove_object(self._bucket, key)
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "MINIO_DELETE_FAILED", f"minio delete_object 失败: {type(exc).__name__}", retryable=True
            ) from exc

    def object_exists(self, key: str) -> bool:
        from minio import S3Error

        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error:
            return False
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "MINIO_STAT_FAILED", f"minio object_exists 失败: {type(exc).__name__}", retryable=True
            ) from exc


class FilesystemStorageBackend(StorageBackend):
    """本地文件系统后端（2026-08-25 · 跨进程共享）

    背景：fake 是进程内单例，uvicorn 上传写完后 worker（另一进程）读不到对象
    （复盘坑 24 "fake 存储进程内单例"）——设备上传链路因此断在 worker 下载照片。
    本地开发/单机部署用 fs 后端：对象落在磁盘目录，多进程共享，零外部依赖。
    路径安全：key 为服务端生成的相对键，拒绝 `..`/绝对路径/反斜杠。
    """

    def __init__(self, root: str | None = None) -> None:
        base = root or settings.fs_storage_root
        p = Path(base)
        if not p.is_absolute():
            p = Path(__file__).resolve().parent.parent.parent.parent / base
        self._root = p
        self._root.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, key: str) -> Path:
        if not key or key.startswith("/") or "\\" in key or ".." in key.split("/"):
            raise ValueError(f"非法对象键: {key}")
        return self._root / key

    def put_object(self, key: str, data: bytes) -> None:
        p = self._safe_path(key)
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_bytes(data)
        except OSError as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "FS_PUT_FAILED", f"fs put_object 失败: {type(exc).__name__}", retryable=False
            ) from exc

    def get_object(self, key: str) -> bytes:
        p = self._safe_path(key)
        if not p.is_file():
            raise KeyError(f"object not found: {key}")
        try:
            return p.read_bytes()
        except OSError as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "FS_GET_FAILED", f"fs get_object 失败: {type(exc).__name__}", retryable=False
            ) from exc

    def delete_object(self, key: str) -> None:
        p = self._safe_path(key)
        if p.is_file():
            try:
                p.unlink()
            except OSError as exc:  # noqa: BLE001 —— 统一包装（P0-6）
                raise StorageError(
                    "FS_DELETE_FAILED", f"fs delete_object 失败: {type(exc).__name__}", retryable=False
                ) from exc

    def object_exists(self, key: str) -> bool:
        return self._safe_path(key).is_file()


def _cos_retryable(exc: Exception) -> bool:
    """COS 异常可重试性分类（P0-6）：网络类(CosClientError)/5xx 可重试"""
    try:
        from qcloud_cos.cos_exception import CosClientError, CosServiceError
    except ImportError:
        return True
    if isinstance(exc, CosClientError):
        return True
    if isinstance(exc, CosServiceError):
        status = exc.get_status_code()
        return status is None or status >= 500
    return True


class CosStorageBackend(StorageBackend):
    """腾讯云 COS（生产；cos-python-sdk-v5）"""

    def __init__(self) -> None:
        from qcloud_cos import CosConfig, CosS3Client

        if not (settings.tencent_secret_id and settings.tencent_secret_key and settings.cos_bucket):
            raise RuntimeError(
                "COS 后端未配置：需要 TENCENT_SECRET_ID/TENCENT_SECRET_KEY/COS_BUCKET/COS_REGION"
            )
        config = CosConfig(
            Region=settings.cos_region,
            SecretId=settings.tencent_secret_id,
            SecretKey=settings.tencent_secret_key,
        )
        self._client = CosS3Client(config)
        self._bucket = settings.cos_bucket

    def put_object(self, key: str, data: bytes) -> None:
        try:
            self._client.put_object(Bucket=self._bucket, Key=key, Body=data)
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "COS_PUT_FAILED", f"COS put_object 失败: {type(exc).__name__}",
                retryable=_cos_retryable(exc),
            ) from exc

    def get_object(self, key: str) -> bytes:
        try:
            resp = self._client.get_object(Bucket=self._bucket, Key=key)
            return resp["Body"].get_raw_stream().read()
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "COS_GET_FAILED", f"COS get_object 失败: {type(exc).__name__}",
                retryable=_cos_retryable(exc),
            ) from exc

    def delete_object(self, key: str) -> None:
        try:
            self._client.delete_object(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "COS_DELETE_FAILED", f"COS delete_object 失败: {type(exc).__name__}",
                retryable=_cos_retryable(exc),
            ) from exc

    def object_exists(self, key: str) -> bool:
        try:
            return self._client.object_exists(Bucket=self._bucket, Key=key)
        except Exception as exc:  # noqa: BLE001 —— 统一包装（P0-6）
            raise StorageError(
                "COS_STAT_FAILED", f"COS object_exists 失败: {type(exc).__name__}",
                retryable=_cos_retryable(exc),
            ) from exc

    def get_sts_credentials(self, user_id: str | None = None) -> dict:
        """STS 临时凭证（客户端直传）——路径级白名单（P0-2 · 审查 H2）

        安全修复：policy resource 从整桶通配 `{bucket}/*` 收紧为当前用户前缀
        `photos|voice|thumbnails/{user_id}/*`，任一登录用户只能写自己前缀，
        防跨用户覆盖/灌入。user_id 缺失直接拒绝（不允许签发整桶凭证）。

        遗留登记：role_arn 仍为 root ARN（腾讯云子账号 role 需独立申请，
        见技术债清理计划 P0-2「root ARN 降级登记」）；若 AssumeRole 失败由调用方
        降级为后端中转。
        """
        from qcloud_cos.sts import Credential

        policy = _build_sts_policy(user_id)
        cred = Credential(
            secret_id=settings.tencent_secret_id,
            secret_key=settings.tencent_secret_key,
            duration_seconds=1800,
            policy=policy,
        )
        return cred.get_credential(
            region=settings.cos_region,
            role_arn=settings.tencent_sts_role_arn,
        )


def _build_sts_policy(user_id: str | None) -> dict:
    """构建路径级白名单 STS policy（P0-2；纯函数，单测直测）

    仅允许当前用户前缀 photos/voice/thumbnails/{user_id}/*；
    拒绝缺失 user_id 或含路径分隔符的 user_id（防前缀逃逸）。
    """
    if not user_id:
        raise ValueError("缺少用户标识，无法签发路径级 STS 凭证（禁止整桶通配）")
    if any(sep in user_id for sep in ("/", "\\", "..")):
        raise ValueError(f"非法用户标识，拒绝签发 STS 凭证: {user_id!r}")
    resource = [
        f"qcs::cos:{settings.cos_region}:uid/{settings.tencent_appid}:"
        f"{settings.cos_bucket}/{prefix}/{user_id}/*"
        for prefix in ("photos", "voice", "thumbnails")
    ]
    return {
        "version": "2.0",
        "statement": [
            {
                "action": [
                    "name/cos:PutObject",
                    "name/cos:PostObject",
                    "name/cos:InitiateMultipartUpload",
                    "name/cos:ListMultipartUploads",
                    "name/cos:ListParts",
                    "name/cos:UploadPart",
                    "name/cos:CompleteMultipartUpload",
                ],
                "effect": "allow",
                "resource": resource,
            }
        ],
    }


_BACKENDS: dict[str, type[StorageBackend]] = {
    "fake": FakeStorageBackend,
    "fs": FilesystemStorageBackend,
    "minio": MinioStorageBackend,
    "cos": CosStorageBackend,
}

# fake 单例（进程内共享内存，供分片跨调用合并）
_FAKE_INSTANCE = FakeStorageBackend()

# cos/minio 进程级单例（S6-10：与 fake 同模式——避免每次调用新建外部连接客户端
# 造成连接/凭证握手开销；懒加载，首次 get 才建）
_MINIO_INSTANCE: MinioStorageBackend | None = None
_COS_INSTANCE: CosStorageBackend | None = None

# fake 容量上限（审查修复 P1/m-14：误配 fake 上生产时防内存无限增长）
FAKE_MAX_OBJECTS = 10000
FAKE_MAX_BYTES = 512 * 1024 * 1024  # 512MB


def reset_storage_backend() -> None:
    """重置存储单例（P2-04：测试隔离——两测试间互不污染）"""
    global _FAKE_INSTANCE, _MINIO_INSTANCE, _COS_INSTANCE  # noqa: PLW0603
    _FAKE_INSTANCE = FakeStorageBackend()
    _MINIO_INSTANCE = None
    _COS_INSTANCE = None


def get_storage_backend(name: str | None = None) -> StorageBackend:
    """存储后端工厂（按 settings.storage_backend 或显式 name）

    fake/minio/cos 均为进程级单例（同一进程内共享实例；fake 供分片跨调用合并，
    minio/cos 避免重复建外部客户端）；fs 每次新建（本地文件系统无外部连接开销）。
    """
    global _MINIO_INSTANCE, _COS_INSTANCE  # noqa: PLW0603
    key = (name or settings.storage_backend or "fake").lower()
    if key not in _BACKENDS:
        raise ValueError(f"未知存储后端: {key}（可选 {sorted(_BACKENDS)}）")
    if key == "fake":
        backend = _FAKE_INSTANCE
        # 容量保护（审查修复）：超限拒绝写入并告警（防内存无界增长）
        if len(backend._store) >= FAKE_MAX_OBJECTS:
            raise RuntimeError(f"fake 存储对象数超限（>{FAKE_MAX_OBJECTS}），检查是否误配 fake 上生产")
        total = sum(len(v) for v in backend._store.values())
        if total >= FAKE_MAX_BYTES:
            raise RuntimeError(f"fake 存储容量超限（>{FAKE_MAX_BYTES // 1024 // 1024}MB），检查是否误配 fake 上生产")
        return backend
    if key == "minio":
        if _MINIO_INSTANCE is None:
            _MINIO_INSTANCE = _BACKENDS["minio"]()
        return _MINIO_INSTANCE
    if key == "cos":
        if _COS_INSTANCE is None:
            _COS_INSTANCE = _BACKENDS["cos"]()
        return _COS_INSTANCE
    return _BACKENDS[key]()
