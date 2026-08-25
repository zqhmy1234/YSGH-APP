"""分片上传状态机（S5-03 COS 分片/断电续传 · WP-C）

流程：init（建任务，幂等）→ upload_chunk（逐片，幂等+校验）→ complete（合并落最终对象）
断点续传：GET status 返回已传分片 → 客户端只补缺失片（不丢不重）。
后端中转方案：分片暂存 staging（storage.put_object uploads/{upload_id}/{index}.part），
complete 时按序合并写最终对象（MVP 照片 ≤3MB-20MB，内存合并可接受；>200MB 走客户端直传预留）。
"""
from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.queue import enqueue_high
from app.db.models import Content, UploadChunk, UploadTask
from app.services.external.storage import get_storage_backend
from app.services.pipeline import process_content
from app.services.thumbnails import derive_thumbnail_key, generate_thumbnail_job, resize_to_jpeg

# 流量约束（B4 §6，Wave3 AgentG）：上传模式白名单
VALID_UPLOAD_MODES = ("original", "thumbnail_meta")

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


def register_photo_content(db: Session, user_id: str, cos_key: str, meta: str) -> str:
    """分片上传集成（S-ST-1 · 2026-08-25）：合并落对象后建 contents 记录 + 入队管线

    语义与 api/contents.py upload_photo 对齐（meta 字段/taken_at ISO/gps 边界/source 白名单），
    否则分片链路与内容管线断裂（对象在存储里但永不进 AI 管线/时间轴）。
    无 perceptual_hash（客户端不计算）→ 不做 409 去重；护栏由管线 CI 审核覆盖。

    Wave3 AgentG 扩展（流量约束 B4 §6）：
      meta.upload_mode ∈ {original, thumbnail_meta}：
        - original（默认）：完整原件，建/更新 contents + 入队管线 + 缩略图预生成
        - thumbnail_meta（蜂窝路径）：上传物即缩略图 → 只落 thumbnail_key 占位内容
          （extra.original_pending=true，status=done，不进管线）；WiFi 后由
          "手动上传原图"（同一 complete 端点 + meta.content_id）补传原件
      meta.content_id：original 模式下若提供 → 更新既有占位内容（补传原件），不新建
      meta.on_wifi：客户端 WiFi 标记（记录到 extra，供流量策略可观测）
    """
    try:
        meta_obj = json.loads(meta) if meta.strip() else {}
        if not isinstance(meta_obj, dict):
            raise ValueError("meta 必须是 JSON 对象")
    except json.JSONDecodeError as exc:
        raise ValueError("meta 必须为合法 JSON 对象") from exc

    upload_mode = meta_obj.get("upload_mode", "original")
    if upload_mode not in VALID_UPLOAD_MODES:
        raise ValueError(f"upload_mode 非法（可选 {'/'.join(VALID_UPLOAD_MODES)}）")
    on_wifi = meta_obj.get("on_wifi")
    if on_wifi is not None and not isinstance(on_wifi, bool):
        raise ValueError("on_wifi 必须为布尔值")

    taken_at = None
    if meta_obj.get("taken_at"):
        try:
            taken_at = datetime.fromisoformat(str(meta_obj["taken_at"]).replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError("taken_at 格式无效（ISO8601）") from exc
    gps_lat = meta_obj.get("gps_lat")
    gps_lng = meta_obj.get("gps_lng")
    try:
        gps_lat = float(gps_lat) if gps_lat is not None else None
        gps_lng = float(gps_lng) if gps_lng is not None else None
    except (TypeError, ValueError) as exc:
        raise ValueError("gps_lat/gps_lng 必须为数值") from exc
    if gps_lat is not None and not (-90 <= gps_lat <= 90):
        raise ValueError("gps_lat 越界（-90~90）")
    if gps_lng is not None and not (-180 <= gps_lng <= 180):
        raise ValueError("gps_lng 越界（-180~180）")
    source = meta_obj.get("source", "app")
    if source not in ("app", "windows", "wechat", "import"):
        raise ValueError("source 非法（可选 app/windows/wechat/import）")
    extra = dict(meta_obj.get("extra")) if isinstance(meta_obj.get("extra"), dict) else {}
    extra["upload_mode"] = upload_mode
    if on_wifi is not None:
        extra["on_wifi"] = on_wifi

    # 蜂窝路径：上传物是缩略图 → 只落缩略图 + 占位内容（等 WiFi 补传原件）
    if upload_mode == "thumbnail_meta":
        thumbnail_key = derive_thumbnail_key(cos_key)
        backend = get_storage_backend()
        data = backend.get_object(cos_key)
        backend.put_object(thumbnail_key, resize_to_jpeg(data))
        extra["original_pending"] = True
        record = Content(
            user_id=user_id,
            content_type="photo",
            taken_at=taken_at,
            gps_lat=gps_lat,
            gps_lng=gps_lng,
            cos_key=cos_key,
            thumbnail_key=thumbnail_key,
            extra=extra,
            source=source,
            status="done",  # 占位即可浏览（缩略图）；原件补传后转 processing 走管线
        )
        db.add(record)
        db.commit()
        db.refresh(record)
        return str(record.id)

    # 手动补传原件（复用 complete）：content_id 指向 thumbnail_meta 占位内容
    content_id = meta_obj.get("content_id")
    if content_id:
        existing = db.get(Content, content_id)
        if existing is None or str(existing.user_id) != str(user_id):
            raise ValueError("content_id 不存在或不属于当前用户")
        existing.cos_key = cos_key
        existing.status = "processing"
        existing.extra = extra
        existing.extra.pop("original_pending", None)
        db.commit()
        enqueue_high(process_content, str(existing.id))
        enqueue_high(generate_thumbnail_job, str(existing.id))
        return str(existing.id)

    record = Content(
        user_id=user_id,
        content_type="photo",
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        cos_key=cos_key,
        extra=extra,
        source=source,
        status="processing",
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    enqueue_high(process_content, str(record.id))
    enqueue_high(generate_thumbnail_job, str(record.id))
    return str(record.id)


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
