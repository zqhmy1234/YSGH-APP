"""对象存储抽象层（S5-03 COS 分片/断电续传 · WP-C）

统一接口，三种实现：
  - FakeStorageBackend：内存字典（单测/默认，零依赖）
  - MinioStorageBackend：MinIO（本地模拟断点续传，S3 兼容，docker 起 minio/minio）
  - CosStorageBackend：腾讯云 COS（生产，cos-python-sdk-v5）

配置：settings.storage_backend ∈ {fake, minio, cos}（默认 fake）
MinIO 连接参数：settings.minio_endpoint / minio_access_key / minio_secret_key / minio_bucket
COS 连接参数：复用 TENCENT_SECRET_ID/SECRET_KEY + COS_BUCKET/COS_REGION（config.py 别名读取已对齐）
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod

from app.core.config import settings

logger = logging.getLogger("yishu.storage")


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

    def get_sts_credentials(self) -> dict:
        """客户端直传临时凭证；不支持的实现抛 NotImplementedError"""
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
            raise RuntimeError(f"minio put_object 失败: {exc}") from exc

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

    def delete_object(self, key: str) -> None:
        self._client.remove_object(self._bucket, key)

    def object_exists(self, key: str) -> bool:
        from minio import S3Error

        try:
            self._client.stat_object(self._bucket, key)
            return True
        except S3Error:
            return False


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
        self._client.put_object(Bucket=self._bucket, Key=key, Body=data)

    def get_object(self, key: str) -> bytes:
        resp = self._client.get_object(Bucket=self._bucket, Key=key)
        return resp["Body"].get_raw_stream().read()

    def delete_object(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=key)

    def object_exists(self, key: str) -> bool:
        return self._client.object_exists(Bucket=self._bucket, Key=key)

    def get_sts_credentials(self) -> dict:
        """STS 临时凭证（客户端直传）——role_arn 现为 root ARN（优化已搁置），
        若 AssumeRole 失败由调用方降级为后端中转"""
        from qcloud_cos.sts import Credential

        cred = Credential(
            secret_id=settings.tencent_secret_id,
            secret_key=settings.tencent_secret_key,
            duration_seconds=1800,
            policy={
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
                        "resource": [
                            f"qcs::cos:{settings.cos_region}:uid/{settings.tencent_appid}:{settings.cos_bucket}/*"
                        ],
                    }
                ],
            },
        )
        return cred.get_credential(
            region=settings.cos_region,
            role_arn=settings.tencent_sts_role_arn,
        )


_BACKENDS: dict[str, type[StorageBackend]] = {
    "fake": FakeStorageBackend,
    "minio": MinioStorageBackend,
    "cos": CosStorageBackend,
}

# fake 单例（进程内共享内存，供分片跨调用合并）
_FAKE_INSTANCE = FakeStorageBackend()

# fake 容量上限（审查修复 P1/m-14：误配 fake 上生产时防内存无限增长）
FAKE_MAX_OBJECTS = 10000
FAKE_MAX_BYTES = 512 * 1024 * 1024  # 512MB


def reset_storage_backend() -> None:
    """重置 fake 单例内存（P2-04：测试隔离——两测试间互不污染）"""
    global _FAKE_INSTANCE  # noqa: PLW0603
    _FAKE_INSTANCE = FakeStorageBackend()


def get_storage_backend(name: str | None = None) -> StorageBackend:
    """存储后端工厂（按 settings.storage_backend 或显式 name）

    fake 为模块级单例（同一进程内共享内存，分片跨调用可合并）；
    minio/cos 每次新建（连接外部服务，无进程内状态）。
    """
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
    return _BACKENDS[key]()
