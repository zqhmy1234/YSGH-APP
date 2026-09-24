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
from collections.abc import Callable
from dataclasses import dataclass
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

    def local_root(self) -> Path | None:
        """本地文件系统根（仅 fs 后端返回）——孤儿分片目录扫描用（P0-3 reaper）；
        fake/minio/cos 返回 None（无磁盘目录可扫/需 list_objects 另行处理）"""
        return None

    def get_sts_credentials(self, user_id: str | None = None) -> dict:
        """客户端直传临时凭证；不支持的实现抛 NotImplementedError

        P0-2（审查 H2）：user_id 必传——policy 按用户前缀签发，禁止整桶通配。
        """
        raise NotImplementedError("该存储后端不支持 STS 临时凭证")

    def get_download_url(self, key: str, user_id: str = "", ttl: int = 900) -> str | None:
        """对象下发 URL（Valet Key / 预签名 · 2026-08-31 新增）

        为什么加这一层：`<image :src>` 带不了 Authorization header，私有对象无法用
        Bearer 下发（移动端历史照片显示不出来的根因）。业界标准是短时效签名 URL。

        Args:
            key: 对象键
            user_id: 归属用户（签进票据，供端点做归属二次校验；COS presigned 不使用）
            ttl: 有效期（秒）——缩略图建议 86400、原图建议 900（见 config.media_*_ttl）

        Returns:
            绝对 URL（COS presigned）或相对路径（`/api/v1/media/{key}?...`，由客户端
            拼 BASE_URL）；**None = 该后端不支持签名下发**，调用方应回退
            `/api/v1/thumbnails/{content_id}` 代理端点（保留其懒生成兜底能力）。
        """
        return None


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

    def get_download_url(self, key: str, user_id: str = "", ttl: int = 900) -> str | None:
        """票据 URL（供单测验证签名/过期/越权三态；与 fs 后端同逻辑）"""
        from app.services.external.media_url import build_media_path

        return build_media_path(key, user_id, ttl)


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


    def get_download_url(self, key: str, user_id: str = "", ttl: int = 900) -> str | None:
        """MinIO 原生预签名（S3 兼容；失败回落 None → 调用方走代理端点）"""
        from datetime import timedelta

        try:
            return self._client.presigned_get_object(
                self._bucket, key, expires=timedelta(seconds=max(int(ttl), 1))
            )
        except Exception as exc:  # noqa: BLE001 —— 签名失败不阻断，回退代理端点
            logger.warning("minio 预签名失败，回退代理端点 key=%s err=%s", key, exc)
            return None


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

    def local_root(self) -> Path | None:
        return self._root

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

    def get_download_url(self, key: str, user_id: str = "", ttl: int = 900) -> str | None:
        """HMAC 票据 URL（相对路径，客户端拼 BASE_URL）

        相对路径是刻意的：fs 后端没有对外可访问的域名概念，让客户端拼自己配置的
        BASE_URL，真机(adb reverse)/模拟器/生产域名三种场景都能自动生效，
        服务端不需要（也不应该）持有 host 配置。
        """
        from app.services.external.media_url import build_media_path

        return build_media_path(key, user_id, ttl)


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
        # B9c/D02-13：客户端构造收敛到**唯一构造点**（与 tencent_ci 共用同一参数口径）
        self._client = build_cos_raw_client()
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

    def get_download_url(self, key: str, user_id: str = "", ttl: int = 900) -> str | None:
        """COS 原生预签名 URL（生产路径；绝对路径，客户端直连 COS 不经过后端）

        这是 Valet Key 的标准形态：签名对象是 COS 的短时效票据，与用户 JWT 无关。
        私有桶 + 短时效 → 即便 URL 外泄，暴露窗口也只有 TTL（原图 15m）。
        失败回落 None（调用方走代理端点），不让图片下发因签名异常而 500。
        """
        try:
            return self._client.get_presigned_url(
                Bucket=self._bucket,
                Key=key,
                Method="GET",
                Expired=max(int(ttl), 1),
            )
        except Exception as exc:  # noqa: BLE001 —— 签名失败回退代理端点
            logger.warning("COS 预签名失败，回退代理端点 key=%s err=%s", key, exc)
            return None

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


@dataclass(frozen=True)
class BackendSpec:
    """一个存储后端的**注册项**（B9c / D02-3 · 2026-09-24 重构波）。

    此前"新增一个存储后端"要改 **7 处**：注册表 + `_FAKE_INSTANCE`/`_MINIO_INSTANCE`/
    `_COS_INSTANCE` 三个单例全局 + `reset_storage_backend` + `get_storage_backend` 的三段
    `if key == ...` 分支 + `core/config.py` 字面量 + `deploy/.env.production.template`。
    现把「构造方式 / 是否单例 / 取用守卫」都声明在**注册项**里 ⇒ **新增后端只改 `_BACKENDS` 1 行**
    （配置与模板那两处仍属部署面，见台账 D02-3 残余说明）。

    · factory   —— 无参构造函数
    · singleton —— 是否**进程级单例**：fake 供分片跨调用合并；minio/cos 避免重复建外部连接
                    客户端（S6-10）；fs 走本地文件系统无连接开销 ⇒ 每次新建（P2-04 语义不变）
    · guard     —— 取用前的守卫（fake 容量保护）；None = 无
    """

    factory: Callable[[], StorageBackend]
    singleton: bool = True
    guard: Callable[[StorageBackend], None] | None = None


def _guard_fake(backend: StorageBackend) -> None:
    """fake 容量保护（审查修复 P1/m-14：误配 fake 上生产时防内存无限增长）"""
    store = backend._store  # type: ignore[attr-defined]  # noqa: SLF001 —— 同模块内守卫
    if len(store) >= FAKE_MAX_OBJECTS:
        raise RuntimeError(f"fake 存储对象数超限（>{FAKE_MAX_OBJECTS}），检查是否误配 fake 上生产")
    total = sum(len(v) for v in store.values())
    if total >= FAKE_MAX_BYTES:
        raise RuntimeError(
            f"fake 存储容量超限（>{FAKE_MAX_BYTES // 1024 // 1024}MB），检查是否误配 fake 上生产"
        )


_BACKENDS: dict[str, BackendSpec] = {
    "fake": BackendSpec(FakeStorageBackend, singleton=True, guard=_guard_fake),
    "fs": BackendSpec(FilesystemStorageBackend, singleton=False),
    "minio": BackendSpec(MinioStorageBackend),
    "cos": BackendSpec(CosStorageBackend),
}

# 单例缓存（仅 singleton=True 的后端入驻）。B9c：原 `_FAKE_INSTANCE`/`_MINIO_INSTANCE`/
# `_COS_INSTANCE` 三个全局收敛为**一个**按 key 索引的字典 —— 消除"每加一个后端就多一个全局"
# 的结构债；fake 也改为**懒建**（构造仅是内存字典，与原"导入期即建"语义等价）。
_INSTANCES: dict[str, StorageBackend] = {}


def build_cos_raw_client() -> object:
    """构建 COS 原始客户端（**唯一构造点** · B9c / D02-13）。

    此前 COS 客户端在**两处**各自构造：`CosStorageBackend.__init__` 与
    `external/tencent_ci.py::_client`（CI 打标/图片审核用）——构造参数与校验条件相同、
    仅报错文案不同 ⇒ 凭证/地域口径有两份，改一处必漏（即"新增存储后端需改 7 处"之一）。
    现统一到此：`CosStorageBackend` 与 `tencent_ci` 均从此取。

    不缓存（与 `tencent_ci` 原"每次调用新建"语义一致）；`CosStorageBackend` 自身仍是
    进程级单例（由 `_BACKENDS` 注册项的 `singleton` 声明）⇒ 生产路径不会重复建客户端。
    """
    from qcloud_cos import CosConfig, CosS3Client

    if not (settings.tencent_secret_id and settings.tencent_secret_key and settings.cos_bucket):
        raise RuntimeError(
            "COS 后端未配置：需要 TENCENT_SECRET_ID/TENCENT_SECRET_KEY/COS_BUCKET/COS_REGION"
        )
    return CosS3Client(
        CosConfig(
            Region=settings.cos_region,
            SecretId=settings.tencent_secret_id,
            SecretKey=settings.tencent_secret_key,
        )
    )


def cos_sts_configured() -> bool:
    """COS/STS 直传配置就绪判定（B9c / D02-13：**由 API 层移入存储层**）。

    原先写在 `api/upload.py`，直读 COS 私有配置字段（`tencent_secret_id`/`cos_bucket`/
    `cos_region`/`tencent_appid`/`tencent_sts_role_arn`）⇒ **API 层越界读存储适配器内部细节**。
    这些字段属存储侧知识，故归位到此；API 只问"能不能走直传"。判定语义与原实现逐字相同。
    """
    return bool(
        settings.tencent_secret_id
        and settings.tencent_secret_key
        and settings.cos_bucket
        and settings.cos_region
        and settings.tencent_appid
        and settings.tencent_sts_role_arn
    )

# fake 容量上限（审查修复 P1/m-14：误配 fake 上生产时防内存无限增长）
FAKE_MAX_OBJECTS = 10000
FAKE_MAX_BYTES = 512 * 1024 * 1024  # 512MB


def reset_storage_backend() -> None:
    """重置存储单例（P2-04：测试隔离——两测试间互不污染）

    B9c：只清**一个**缓存字典（原需逐个 reset 三个全局）；下次取用时按注册项懒建。
    """
    _INSTANCES.clear()


def get_storage_backend(name: str | None = None) -> StorageBackend:
    """存储后端工厂（按 settings.storage_backend 或显式 name）

    B9c（D02-3）：**新增一个存储后端 = 在 `_BACKENDS` 加 1 行**。
    单例语义（fake/minio/cos 进程级共享；fs 每次新建）与取用守卫（fake 容量保护）
    由注册项 `BackendSpec` 声明，本函数只做「解析 → 取/建 → 守卫」三步，不再有
    按后端名分叉的 `if key == ...`（原三段分支 + 两个单例全局 + reset 的重置列表均已消除）。

    语义与重构前**逐点等价**：
      · fake：进程级共享（分片跨调用合并）＋ 每次取用都跑容量守卫；
      · minio/cos：懒建进程级单例（避免重复建外部客户端，S6-10）；
      · fs：每次新建（无外部连接开销）；
      · 未知后端：`ValueError`（文案不变）。
    """
    key = (name or settings.storage_backend or "fake").lower()
    spec = _BACKENDS.get(key)
    if spec is None:
        raise ValueError(f"未知存储后端: {key}（可选 {sorted(_BACKENDS)}）")
    backend = _INSTANCES.get(key)
    if backend is None:
        backend = spec.factory()
        if spec.singleton:
            _INSTANCES[key] = backend
    if spec.guard is not None:
        spec.guard(backend)
    return backend
