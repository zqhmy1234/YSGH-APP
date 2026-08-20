"""分片上传状态机（S5-03 COS 分片/断电续传 · WP-C）

流程：init（建任务，幂等）→ upload_chunk（逐片，幂等+校验）→ complete（合并落最终对象）
断点续传：GET status 返回已传分片 → 客户端只补缺失片（不丢不重）。
后端中转方案：分片暂存 staging（storage.put_object uploads/{upload_id}/{index}.part），
complete 时按序合并写最终对象（MVP 照片 ≤3MB-20MB，内存合并可接受；>200MB 走客户端直传预留）。
"""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import UploadChunk, UploadTask
from app.services.external.storage import get_storage_backend

# 后端中转合并上限（超限建议客户端直传，MVP 照片远低于此）
MAX_INLINE_MERGE_BYTES = 200 * 1024 * 1024
DEFAULT_CHUNK_SIZE = 8 * 1024 * 1024  # 8MB（对齐微信图片 3MB 与通用分片习惯）


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
        raise ValueError("file_size 必须 > 0")
    if chunk_size <= 0:
        raise ValueError("chunk_size 必须 > 0")

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
        raise KeyError(f"上传任务不存在: {task.id}")


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
        raise KeyError(f"上传任务不存在: {upload_id}")
    if user_id is not None:
        _assert_owner(task, user_id)
    if task.status == "completed":
        return {"status": "completed", "detail": "任务已完成"}

    # 审查修复(P1-02)：分片大小校验——客户端自报 chunk_size 不可信，
    # 单片超限直接拒绝（此前无校验，可单片传超大块吃满内存/存储）。
    if len(data) > task.chunk_size:
        raise ValueError(f"分片过大: {len(data)} > 声明分片大小 {task.chunk_size}")

    actual_hash = _sha256(data)
    if chunk_hash and chunk_hash != actual_hash:
        raise ValueError(f"分片哈希不匹配: 期望 {chunk_hash} 实际 {actual_hash[:16]}…")
    if not (0 <= chunk_index < task.chunk_count):
        raise ValueError(f"chunk_index 越界: {chunk_index} (0..{task.chunk_count - 1})")

    existing = db.scalar(
        select(UploadChunk).where(
            UploadChunk.upload_id == upload_id,
            UploadChunk.chunk_index == chunk_index,
        )
    )
    if existing:
        if existing.chunk_hash == actual_hash:
            return {"status": "duplicate", "chunk_index": chunk_index}
        raise ValueError("同 index 已存在不同内容的片（客户端状态异常）")

    backend = get_storage_backend(task.storage)
    backend.put_object(_staging_key(upload_id, chunk_index), data)

    db.add(
        UploadChunk(
            id=str(uuid.uuid4()),
            upload_id=upload_id,
            chunk_index=chunk_index,
            chunk_hash=actual_hash,
            size=len(data),
            status="uploaded",
        )
    )
    if task.status == "pending":
        task.status = "uploading"
    db.commit()
    return {"status": "uploaded", "chunk_index": chunk_index}


def get_status(db: Session, upload_id: str, user_id: str | None = None) -> dict:
    """断点续传状态：已传分片列表 + 缺失分片列表"""
    task = db.get(UploadTask, upload_id)
    if task is None:
        raise KeyError(f"上传任务不存在: {upload_id}")
    if user_id is not None:
        _assert_owner(task, user_id)
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
        raise KeyError(f"上传任务不存在: {upload_id}")
    if user_id is not None:
        _assert_owner(task, user_id)
    if task.status == "completed":
        return {"status": "completed", "file_key": task.file_key, "upload_id": upload_id}

    status = get_status(db, upload_id)
    if status["missing_chunks"]:
        raise ValueError(f"分片未齐: 缺 {status['missing_chunks']}")

    if task.file_size > MAX_INLINE_MERGE_BYTES:
        raise ValueError("文件过大，请走客户端直传（>200MB）")

    backend = get_storage_backend(task.storage)
    buf = bytearray()
    for i in range(task.chunk_count):
        buf.extend(backend.get_object(_staging_key(upload_id, i)))
    merged = bytes(buf)
    if len(merged) != task.file_size:
        raise ValueError(f"合并后大小不符: 期望 {task.file_size} 实际 {len(merged)}")

    backend.put_object(task.file_key, merged)
    for i in range(task.chunk_count):
        backend.delete_object(_staging_key(upload_id, i))

    task.status = "completed"
    task.completed_at = func.now()
    db.commit()
    return {"status": "completed", "file_key": task.file_key, "upload_id": upload_id}
