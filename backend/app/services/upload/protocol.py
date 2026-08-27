"""分片上传协议层（S5-03 COS 分片/断电续传 · WP-C · R1#18 拆自 upload.py）

职责：分片上传状态机——init（建任务，幂等）→ upload_chunk（逐片，幂等+校验）
→ complete（合并落最终对象）→ get_status（断点续传：已传/缺失分片）。
只负责"协议"（任务/分片/合并/归属校验），内容注册见同包 register.py。

后端中转方案：分片暂存 staging（storage.put_object uploads/{upload_id}/{index}.part），
complete 时按序合并写最终对象（MVP 照片 ≤3MB-20MB，内存合并可接受；>200MB 走客户端直传预留）。
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import UploadChunk, UploadTask
from app.services.errors import ConflictError, NotFoundError, TooLargeError, ValidationError
from app.services.external.storage import best_effort_delete, get_storage_backend

logger = logging.getLogger("yishu.upload")

# 流量约束（B4 §6，Wave3 AgentG）：上传模式白名单
VALID_UPLOAD_MODES = ("original", "thumbnail_meta")

# 后端中转合并上限（超限建议客户端直传，MVP 照片远低于此）
MAX_INLINE_MERGE_BYTES = 200 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB（对齐微信图片 3MB 与通用分片习惯）

# ---- TD-P3 M1 修复（审查中危）：大数构造 DoS ----
# 攻击面：file_size=10^12 + chunk_size=1 → chunk_count≈10^12 → get_status
# 物化缺失列表 OOM 击穿 API 进程。三重防御：
#   1. file_size 上限（≤500MB，与任务清单一致）
#   2. chunk_size 下限（防 chunk_count 爆炸；1KB 下限兼容测试用小分片）
#   3. get_status 分片数守卫（防迁移前遗留的恶意任务行仍触发 OOM）
MAX_UPLOAD_FILE_SIZE = 500 * 1024 * 1024   # 500MB
MIN_CHUNK_SIZE = 1024                       # 1KB
MAX_CHUNK_COUNT = 100_000                   # get_status 防御性上限（超出视为异常任务）


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _staging_key(upload_id: str, index: int) -> str:
    return f"uploads/{upload_id}/{index}.part"


def _final_key(user_id: str, file_name: str) -> str:
    safe = "".join(c for c in file_name if c.isalnum() or c in "._-") or "file"
    now = datetime.now(timezone.utc)
    return f"photos/{user_id}/{now:%Y%m}/{uuid.uuid4().hex[:12]}_{safe}"


def init_upload(
    db: Session,
    user_id: str,
    client_upload_id: str,
    file_name: str,
    file_size: int,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
    storage: str | None = None,
) -> UploadTask:
    """创建/复用上传任务（client_upload_id 幂等）"""
    if file_size <= 0:
        raise ValidationError("file_size 必须 > 0")
    if file_size > MAX_UPLOAD_FILE_SIZE:
        raise ValidationError(f"file_size 超过上限（{MAX_UPLOAD_FILE_SIZE // (1024 * 1024)}MB）")
    if chunk_size <= 0:
        raise ValidationError("chunk_size 必须 > 0")
    if chunk_size < MIN_CHUNK_SIZE:
        raise ValidationError(f"chunk_size 过小（最小 {MIN_CHUNK_SIZE} 字节）")

    existing = db.scalar(
        select(UploadTask).where(
            UploadTask.user_id == user_id,
            UploadTask.client_upload_id == client_upload_id,
        )
    )
    if existing:
        return existing

    chunk_count = max(1, (file_size + chunk_size - 1) // chunk_size)
    task = UploadTask(
        id=str(uuid.uuid4()),
        user_id=user_id,
        client_upload_id=client_upload_id,
        file_name=file_name,
        file_size=file_size,
        chunk_size=chunk_size,
        chunk_count=chunk_count,
        file_key=_final_key(user_id, file_name),
        storage=storage or settings.storage_backend or "fake",
        status="pending",
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


def _assert_owner(task: UploadTask, user_id: str) -> None:
    """归属校验（审查 CRITICAL 修复）：任务必须属于当前用户，否则按不存在处理"""
    if str(task.user_id) != str(user_id):
        raise NotFoundError(f"上传任务不存在: {task.id}")


def upload_chunk(
    db: Session,
    upload_id: str,
    chunk_index: int,
    data: bytes,
    chunk_hash: str | None = None,
    user_id: str | None = None,
) -> dict:
    """上传单片（幂等：同 index 同 hash 返回 duplicate；同 index 异 hash 拒绝）"""
    task = db.get(UploadTask, upload_id)
    if task is None:
        raise NotFoundError(f"上传任务不存在: {upload_id}")
    if user_id is not None:
        _assert_owner(task, user_id)
    if task.status == "completed":
        return {"status": "completed", "detail": "任务已完成"}

    # 审查修复(P1-02)：分片大小校验——客户端自报 chunk_size 不可信，
    # 单片超限直接拒绝（此前无校验，可单片传超大块吃满内存/存储）。
    if len(data) > task.chunk_size:
        raise ValidationError(f"分片过大: {len(data)} > 声明分片大小 {task.chunk_size}")

    actual_hash = _sha256(data)
    if chunk_hash and chunk_hash != actual_hash:
        raise ValidationError(f"分片哈希不匹配: 期望 {chunk_hash} 实际 {actual_hash[:16]}…")
    if not (0 <= chunk_index < task.chunk_count):
        raise ValidationError(f"chunk_index 越界: {chunk_index} (0..{task.chunk_count - 1})")

    existing = db.scalar(
        select(UploadChunk).where(
            UploadChunk.upload_id == upload_id,
            UploadChunk.chunk_index == chunk_index,
        )
    )
    if existing:
        if existing.chunk_hash == actual_hash:
            return {"status": "duplicate", "chunk_index": chunk_index}
        raise ConflictError("同 index 已存在不同内容的片（客户端状态异常）")

    backend = get_storage_backend(task.storage)
    backend.put_object(_staging_key(upload_id, chunk_index), data)

    # R2#13 竞态修复：同片并发上传 → ON CONFLICT DO NOTHING 原子兜底。
    # SELECT 查重是快路径；并发窗口内两请求同时插同 (upload_id, chunk_index)
    # （UNIQUE uq_upload_chunks_task_index），败者不再 IntegrityError 500 →
    # rowcount=0 → 按哈希判定 duplicate（同内容）/ 冲突（异内容，客户端状态异常）。
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    inserted = db.execute(
        pg_insert(UploadChunk)
        .values(
            id=str(uuid.uuid4()),
            upload_id=upload_id,
            chunk_index=chunk_index,
            chunk_hash=actual_hash,
            size=len(data),
            status="uploaded",
        )
        .on_conflict_do_nothing(
            index_elements=[UploadChunk.upload_id, UploadChunk.chunk_index]
        )
    )
    if inserted.rowcount == 0:
        winner = db.scalar(
            select(UploadChunk).where(
                UploadChunk.upload_id == upload_id,
                UploadChunk.chunk_index == chunk_index,
            )
        )
        if winner is not None and winner.chunk_hash == actual_hash:
            return {"status": "duplicate", "chunk_index": chunk_index}
        raise ConflictError("同 index 已存在不同内容的片（客户端状态异常）")

    if task.status == "pending":
        task.status = "uploading"
    db.commit()
    return {"status": "uploaded", "chunk_index": chunk_index}


def get_status(db: Session, upload_id: str, user_id: str | None = None) -> dict:
    """断点续传状态：已传分片列表 + 缺失分片列表"""
    task = db.get(UploadTask, upload_id)
    if task is None:
        raise NotFoundError(f"上传任务不存在: {upload_id}")
    if user_id is not None:
        _assert_owner(task, user_id)
    # TD-P3 M1 守卫：分片数异常（迁移前恶意任务行）直接拒绝，不物化巨型缺失列表
    if task.chunk_count > MAX_CHUNK_COUNT:
        raise ValidationError(f"任务分片数异常（{task.chunk_count}），请重新上传")
    chunks = db.scalars(
        select(UploadChunk.chunk_index)
        .where(UploadChunk.upload_id == upload_id)
        .order_by(UploadChunk.chunk_index)
    ).all()
    uploaded = sorted(chunks)
    missing = [i for i in range(task.chunk_count) if i not in set(uploaded)]
    return {
        "upload_id": upload_id,
        "status": task.status,
        "chunk_count": task.chunk_count,
        "uploaded_chunks": uploaded,
        "missing_chunks": missing,
    }


def complete_upload(db: Session, upload_id: str, user_id: str | None = None) -> dict:
    """合并分片 → 落最终对象（幂等：已完成直接返回）"""
    task = db.get(UploadTask, upload_id)
    if task is None:
        raise NotFoundError(f"上传任务不存在: {upload_id}")
    if user_id is not None:
        _assert_owner(task, user_id)
    if task.status == "completed":
        return {"status": "completed", "file_key": task.file_key, "upload_id": upload_id}

    status = get_status(db, upload_id)
    if status["missing_chunks"]:
        raise ConflictError(f"分片未齐: 缺 {status['missing_chunks']}")

    if task.file_size > MAX_INLINE_MERGE_BYTES:
        raise TooLargeError("文件过大，请走客户端直传（>200MB）")

    backend = get_storage_backend(task.storage)
    buf = bytearray()
    for i in range(task.chunk_count):
        buf.extend(backend.get_object(_staging_key(upload_id, i)))
    merged = bytes(buf)
    if len(merged) != task.file_size:
        raise ValidationError(f"合并后大小不符: 期望 {task.file_size} 实际 {len(merged)}")

    backend.put_object(task.file_key, merged)
    for i in range(task.chunk_count):
        backend.delete_object(_staging_key(upload_id, i))

    task.status = "completed"
    task.completed_at = func.now()
    try:
        db.commit()
    except Exception:  # noqa: BLE001 —— P0-6（审查 H-3）：对象已落但 DB 未提交
        # → 尽力删除最终对象防孤儿（staging 分片已删，任务留 pending 可重试）
        db.rollback()
        best_effort_delete(task.file_key, backend)
        raise
    return {"status": "completed", "file_key": task.file_key, "upload_id": upload_id}
