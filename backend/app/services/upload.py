"""分片上传状态机（S5-03 COS 分片/断电续传 · WP-C）

流程：init（建任务，幂等）→ upload_chunk（逐片，幂等+校验）→ complete（合并落最终对象）
断点续传：GET status 返回已传分片 → 客户端只补缺失片（不丢不重）。
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
from app.db.models import Content, UploadChunk, UploadTask
from app.services.errors import ConflictError, NotFoundError, TooLargeError, ValidationError
from app.services.external.storage import best_effort_delete, get_storage_backend
from app.services.photo_content import (
    register_photo_content as photo_register,
)
from app.services.photo_content import (
    safe_enqueue_unique,
)
from app.services.pipeline import process_content
from app.services.upload_meta import MetaValidationError, parse_photo_meta

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
    # 共享 meta 校验（TD-P2B · S1-H2 收口）：taken_at ISO/gps 边界/source 白名单统一走
    # upload_meta.parse_photo_meta，与 api/contents.py upload_photo 同一契约（此前双份
    # 复制靠注释"对齐"，已出现 except 分支/常量漂移）；异常统一转 ValueError。
    try:
        photo_meta = parse_photo_meta(meta)
    except MetaValidationError as exc:
        raise ValidationError(str(exc)) from exc
    meta_obj = photo_meta.raw

    # 协议层校验：upload_mode / on_wifi / content_type
    upload_mode = meta_obj.get("upload_mode", "original")
    if upload_mode not in VALID_UPLOAD_MODES:
        raise ValidationError(f"upload_mode 非法（可选 {'/'.join(VALID_UPLOAD_MODES)}）")
    on_wifi = meta_obj.get("on_wifi")
    if on_wifi is not None and not isinstance(on_wifi, bool):
        raise ValidationError("on_wifi 必须为布尔值")
    extra = dict(meta_obj.get("extra")) if isinstance(meta_obj.get("extra"), dict) else {}
    extra["upload_mode"] = upload_mode
    if on_wifi is not None:
        extra["on_wifi"] = on_wifi

    # B5a 集成（Wave4 AgentJ 需求 1）：complete 直接建 voice 内容（客户端 uploadVoicePersistent
    # 契约：meta.content_type=voice + duration_ms + source + extra.file_name）
    content_type = meta_obj.get("content_type", "photo")
    if content_type not in ("photo", "voice"):
        raise ValidationError("content_type 非法（可选 photo/voice）")
    if content_type == "voice":
        return _register_voice_content(
            db, user_id, cos_key, meta_obj, extra, photo_meta.taken_at,
            photo_meta.gps_lat, photo_meta.gps_lng, photo_meta.source,
        )

    # 照片注册统一委托 services/photo_content（F1/P0-6 双轨收口）：dedup_key="cos_key"
    # 幂等（P0-5）+ 无 moderate（护栏由管线 CI 审核覆盖）+ mode 驱动
    # original/thumbnail_meta/update；本函数只做协议适配，两套幂等键保留。
    content_id = meta_obj.get("content_id")
    mode = "update" if content_id else upload_mode
    return photo_register(
        db,
        user_id,
        dedup_key="cos_key",
        moderate=False,
        mode=mode,
        meta_obj=meta_obj,
        photo_meta=photo_meta,
        cos_key=cos_key,
        extra=extra,
        content_id=content_id,
        enqueue_thumbnail=True,
    )


def _register_voice_content(
    db: Session,
    user_id: str,
    cos_key: str,
    meta: dict,
    extra: dict,
    taken_at,
    gps_lat,
    gps_lng,
    source: str,
) -> str:
    """B5a 集成（Wave4 AgentJ 需求 1）：complete 直接建 voice 内容（不再落 stray photo）。

    - 对象从 photos/ 前缀搬到 voice/{user_id}/ 前缀（语音与照片隔离，生命周期/CDN 可分别配置）
    - 建 content_type=voice 记录 + 入队 process_content（走 ASR/VAD 转写）；voice 无缩略图不入队
    - 幂等：同用户同 cos_key 已存在 → 直接返回既有 content_id（旧客户端仍会二次建内容请求）
    """
    existing = db.scalar(
        select(Content).where(
            Content.user_id == user_id,
            Content.cos_key == cos_key,
            Content.deleted_at.is_(None),
        )
    )
    if existing is not None:
        return str(existing.id)

    # 搬移到 voice/ 前缀（storage 后端 get/put/delete；fake 后端同样可用）
    backend = get_storage_backend()
    try:
        data = backend.get_object(cos_key)
    except KeyError:
        data = None
    if not data:
        raise NotFoundError("对象存储中未找到已上传文件")
    raw_extra = meta.get("extra")
    file_name = raw_extra.get("file_name", "audio") if isinstance(raw_extra, dict) else "audio"
    safe = "".join(c for c in str(file_name) if c.isalnum() or c in "._-") or "audio"
    now = datetime.now(timezone.utc)
    voice_key = f"voice/{user_id}/{now:%Y%m}/{uuid.uuid4().hex[:12]}_{safe}"
    backend.put_object(voice_key, data)
    try:
        backend.delete_object(cos_key)
    except Exception:  # noqa: BLE001 —— 旧键删除失败不阻断（孤儿对象由清理任务兜底）
        logger.warning("voice 上传旧键删除失败 cos_key=%s", cos_key)

    voice_extra = dict(extra)
    duration_ms = meta.get("duration_ms")
    if duration_ms is not None:
        try:
            voice_extra["duration_ms"] = int(duration_ms)
        except (TypeError, ValueError):
            raise ValidationError("duration_ms 必须为整数（毫秒）") from None

    record = Content(
        user_id=user_id,
        content_type="voice",
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        cos_key=voice_key,
        extra=voice_extra,
        source=source,
        status="processing",
    )
    db.add(record)
    try:
        db.commit()
    except Exception:  # noqa: BLE001 —— P0-6：提交失败尽力删新键防孤儿
        db.rollback()
        best_effort_delete(voice_key, backend)
        raise
    db.refresh(record)
    # F4：enqueue_unique 同 content 键不重复入队（safe：失败仅记日志，P0-5）
    safe_enqueue_unique(process_content, str(record.id))
    return str(record.id)


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
