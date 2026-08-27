"""上传内容注册层（R1#18 拆自 upload.py：协议适配与注册逻辑，与 protocol.py 分离）

职责：complete 后把已落对象注册为 contents 记录 + 入队管线——
  - photo：经 services/photo_content.register_photo_content 收口（F1/P0-6 双轨），
    本层只做协议适配（upload_mode/on_wifi/content_type 校验 + meta 解析）
  - voice：B5a 集成（complete 直接建 voice 内容，对象搬 voice/ 前缀 + 入队 ASR）

注意：本文件现含 F-Content（photo_content 收口）+ P0（魔数嗅探/幂等）成果，
不得回退。协议状态机（init/upload_chunk/complete/get_status）在同包 protocol.py。
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content
from app.services.errors import NotFoundError, ValidationError
from app.services.external.storage import best_effort_delete, get_storage_backend
from app.services.photo_content import (
    register_photo_content as photo_register,
)
from app.services.photo_content import (
    safe_enqueue_unique,
)
from app.services.pipeline import process_content
from app.services.upload.protocol import VALID_UPLOAD_MODES
from app.services.upload_meta import MetaValidationError, parse_photo_meta

logger = logging.getLogger("yishu.upload")


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
