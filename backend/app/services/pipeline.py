"""内容 AI 管线（P2-02 重构：从 workers/worker.py 下沉，worker 只留进程入口）

process_content(content_id)：按 content_type 分流处理，全部异步在 RQ worker 执行。
设计：
- 非关键增强步骤失败可静默降级；ASR 主步骤失败必须显式回写 failed
- text  → SetFit 分类 → 写 content_class/class_source/model_version → 索引
- voice → ASR 转写 → succeeded/no_speech/failed_* → 分类 → 索引
- photo → image_caption（Qwen3-VL）写 caption 入索引 + CI 打标（失败静默）
- 全部 → 事件聚合（aggregate_user，失败静默）
- 状态机：processing → done/failed；空白语音以 done + audio_processing.no_speech 表达

依赖方向：api → services.pipeline → services.*（单向，不再反向 import worker）。
"""
from __future__ import annotations

import logging
import uuid
from functools import lru_cache
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Content
from app.db.session import SessionLocal

logger = logging.getLogger("yishu.pipeline")


@lru_cache(maxsize=1)
def _classifier_fn():
    """SetFit classify 函数（进程内单例）"""
    from app.services.classifier import classify

    return classify


def _get_classifier():
    return _classifier_fn()


def _index_content(db: Session, content: Content, text: str | None = None) -> None:
    """内容入向量库（B2：dense+sparse 索引进 yishu_contents）"""
    from app.services.embedding import encode_dense, encode_sparse
    from app.services.vector_store import get_store

    payload_text = text or content.text
    if not payload_text:
        return
    dense = encode_dense([payload_text])[0]
    sparse = encode_sparse([payload_text])[0]
    store = get_store()
    # 审查 CRITICAL 修复：taken_at 转 epoch 秒（int）落 payload，与 _to_filter 的
    # Range 数值过滤一致——此前存 ISO 字符串导致时间过滤在生产库静默不命中。
    taken_ts = int(content.taken_at.timestamp()) if content.taken_at else None
    # Wave0 钩子：B2 域扩展 payload（place/tags/content_type 归一）——冻结 pipeline.py 后唯一扩展入口
    from app.services.pipeline_ext import extend_payload

    payload = extend_payload(
        content,
        {
            "content_type": content.content_type,
            "content_class": content.content_class,
            "taken_at": taken_ts,
            "user_id": str(content.user_id),
        },
    )
    store.upsert_content(
        content_id=str(content.id),
        text=payload_text,
        dense=dense,
        sparse=sparse,
        payload=payload,
    )


def _classify_content(db: Session, content: Content, text: str) -> None:
    """SetFit 5 类分类 → 回写 content_class/class_source/model_version"""
    try:
        result = _get_classifier()(text)
        content.content_class = result["label"]
        content.class_source = "setfit"
        content.model_version = "setfit-v1"
    except Exception as exc:  # noqa: BLE001 —— 分类失败静默（用户无感知）
        logger.warning("分类失败 content=%s: %s", content.id, exc)


def _process_text(db: Session, content: Content) -> None:
    if content.text:
        _classify_content(db, content, content.text)
        _index_content(db, content, content.text)
        # Wave0 钩子：B5b 敏感标记 + B1 画像标注
        from app.services.pipeline_ext import annotate_on_ingest, mark_sensitive_on_ingest

        mark_sensitive_on_ingest(db, content)
        annotate_on_ingest(db, content)


def _set_audio_processing(content: Content, payload: dict) -> None:
    extra = dict(content.extra or {})
    extra["audio_processing"] = payload
    if payload.get("outcome") in {"succeeded", "no_speech", "mock"}:
        extra.pop("error", None)
    content.extra = extra


def _materialize_voice_audio(content: Content) -> tuple[Path, Path | None]:
    """把 COS 音频下载为临时文件；本地测试路径直接复用。"""
    import tempfile

    from app.services.external.asr import (
        AsrError,
        temporary_suffix,
        validate_audio_bytes,
    )

    if content.cos_key:
        from app.services.external.storage import get_storage_backend

        try:
            data = get_storage_backend().get_object(content.cos_key)
        except Exception as exc:  # noqa: BLE001
            raise AsrError(
                "AUDIO_DOWNLOAD_FAILED",
                "语音文件下载失败",
                retryable=True,
            ) from exc
        filename = str((content.extra or {}).get("file_name") or content.cos_key)
        # 内部对象存储允许长 WAV 进入 VAD 分段；API 直传仍保持 8MB 上限。
        audio_format = validate_audio_bytes(data, filename, max_bytes=None)
        with tempfile.NamedTemporaryFile(
            suffix=temporary_suffix(audio_format), delete=False
        ) as tmp:
            tmp.write(data)
            tmp_file = Path(tmp.name)
        return tmp_file, tmp_file

    if content.extra and content.extra.get("audio_path"):
        return Path(content.extra["audio_path"]), None
    raise AsrError("AUDIO_NOT_FOUND", "语音内容缺少可处理的音频文件")


def _cleanup_temporary_audio(tmp_file: Path | None) -> None:
    if tmp_file is None:
        return
    try:
        tmp_file.unlink(missing_ok=True)
    except OSError:
        pass


def _set_emotion_enrichment(
    content: Content,
    status: str,
    *,
    error: dict | None = None,
) -> None:
    extra = dict(content.extra or {})
    detail = dict(extra.get("audio_processing") or {})
    detail["emotion_enrichment"] = status
    if error is None:
        detail.pop("emotion_error", None)
    else:
        detail["emotion_error"] = error
    extra["audio_processing"] = detail
    content.extra = extra


def _process_voice(db: Session, content: Content) -> str:
    """语音主步骤：先完成转写；本地情绪由独立低优先级任务增强。"""

    from app.services.external.asr import (
        EMOTION_ACTION_THRESHOLD,
        AsrError,
        should_enhance_with_local_emotion,
        transcribe,
    )

    tmp_file: Path | None = None
    try:
        audio_path, tmp_file = _materialize_voice_audio(content)
        result = transcribe(str(audio_path), enhance_emotion=False)
        if result.mock and settings.app_env == "production":
            raise AsrError("MOCK_RESULT_IN_PRODUCTION", "生产环境禁止保存 mock 转写")

        audit = result.audit_dict()
        needs_local_emotion = (
            not result.mock and should_enhance_with_local_emotion(result)
        )
        audit["emotion_enrichment"] = (
            "pending" if needs_local_emotion else "not_needed"
        )
        _set_audio_processing(content, audit)
        if result.outcome == "no_speech":
            return "no_speech"
        if not result.text.strip():
            raise AsrError(
                "EMPTY_TRANSCRIPT",
                "ASR 返回空文本但未标记为空白语音",
                retryable=True,
            )

        content.text = result.text
        content.emotion = {
            "emotion": result.emotion,
            "confidence": result.emotion_confidence,
            "source": result.emotion_source,
            "model": result.emotion_model,
            "actionable": (
                result.emotion != "平静"
                and result.emotion_confidence >= EMOTION_ACTION_THRESHOLD
            ),
        }
        _classify_content(db, content, result.text)
        # Wave0 钩子：B1 画像标注（语音语义内容）+ B5a 情绪消费
        from app.services.pipeline_ext import annotate_on_ingest, consume_emotion

        annotate_on_ingest(db, content)
        consume_emotion(db, content)
        try:
            _index_content(db, content, result.text)
        except Exception as exc:  # noqa: BLE001 -- 索引失败不否定已完成的真实转写
            logger.warning("语音索引失败 content=%s: %s", content.id, exc)
            extra = dict(content.extra or {})
            extra["index_error"] = type(exc).__name__
            content.extra = extra
        return result.outcome
    finally:
        _cleanup_temporary_audio(tmp_file)


def enrich_content_emotion(content_id: str) -> dict:
    """低优先级 RQ 任务：只增强情绪，不改变已完成的转写状态。"""
    from app.services.external.asr import (
        EMOTION_ACTION_THRESHOLD,
        MODEL_SENSEVOICE,
        AsrError,
        infer_local_emotion,
    )

    db: Session = SessionLocal()
    content: Content | None = None
    tmp_file: Path | None = None
    try:
        content = db.get(Content, content_id)
        if content is None:
            return {"content_id": content_id, "status": "not-found"}
        if content.content_type != "voice" or not (content.text or "").strip():
            _set_emotion_enrichment(content, "skipped")
            db.commit()
            return {"content_id": content_id, "status": "skipped"}

        current_source = str((content.emotion or {}).get("source") or "none")
        mode = settings.asr_local_emotion_mode
        if mode == "off" or (
            mode == "auto" and current_source not in {"", "none"}
        ):
            _set_emotion_enrichment(content, "skipped")
            db.commit()
            return {
                "content_id": content_id,
                "status": "skipped",
                "reason": "disabled" if mode == "off" else "primary-emotion-present",
            }

        _set_emotion_enrichment(content, "processing")
        db.commit()
        audio_path, tmp_file = _materialize_voice_audio(content)
        local = infer_local_emotion(audio_path)
        actionable = (
            local.emotion != "平静"
            and local.emotion_confidence >= EMOTION_ACTION_THRESHOLD
        )
        content.emotion = {
            "emotion": local.emotion,
            "confidence": local.emotion_confidence,
            "source": "sensevoice_local",
            "model": MODEL_SENSEVOICE,
            "actionable": actionable,
        }
        extra = dict(content.extra or {})
        detail = dict(extra.get("audio_processing") or {})
        detail.update(
            {
                "emotion": local.emotion,
                "emotion_confidence": local.emotion_confidence,
                "emotion_source": "sensevoice_local",
                "emotion_model": MODEL_SENSEVOICE,
                "emotion_actionable": actionable,
                "emotion_enrichment": "succeeded",
            }
        )
        detail.pop("emotion_error", None)
        extra["audio_processing"] = detail
        content.extra = extra
        db.commit()
        return {"content_id": content_id, "status": "succeeded"}
    except AsrError as exc:
        db.rollback()
        target = db.get(Content, content_id)
        if target is not None:
            _set_emotion_enrichment(
                target,
                "failed",
                error={"code": exc.code, "retryable": exc.retryable},
            )
            db.commit()
        logger.warning("本地情绪增强失败 content=%s: %s", content_id, exc.code)
        return {"content_id": content_id, "status": "failed", "error": exc.code}
    except Exception as exc:  # noqa: BLE001 -- 情绪失败不回滚主转写
        db.rollback()
        target = db.get(Content, content_id)
        if target is not None:
            _set_emotion_enrichment(
                target,
                "failed",
                error={"code": "LOCAL_EMOTION_PIPELINE_ERROR", "retryable": True},
            )
            db.commit()
        logger.warning("本地情绪增强异常 content=%s: %s", content_id, type(exc).__name__)
        return {
            "content_id": content_id,
            "status": "failed",
            "error": "LOCAL_EMOTION_PIPELINE_ERROR",
        }
    finally:
        _cleanup_temporary_audio(tmp_file)
        db.close()


def _process_photo(db: Session, content: Content) -> None:
    """照片：image_caption 写 caption 入索引 + CI 打标（都失败静默）

    审查 CRITICAL 修复：image_caption 需要本地文件路径，cos_key 是对象存储键——
    先下载到临时文件再调用（与 _process_voice 一致），修复真实链路必然失败问题。
    """
    import tempfile

    from app.services.external.dashscope import image_caption

    # 取 COS 图片（cos_key）或本地路径（extra.image_path，测试用）
    image_path = None
    tmp_file = None
    if content.cos_key:
        try:
            from app.services.external.storage import get_storage_backend

            storage = get_storage_backend()
            data = storage.get_object(content.cos_key)
            tmp_file = Path(tempfile.gettempdir()) / f"yishu_photo_{content.id}.jpg"
            tmp_file.write_bytes(data)
            image_path = tmp_file
        except Exception as exc:  # noqa: BLE001
            logger.warning("图片下载失败 content=%s: %s", content.id, exc)
    elif content.extra and content.extra.get("image_path"):
        image_path = Path(content.extra["image_path"])

    try:
        # 1. caption（图片塔；失败不影响照片浏览，仅不可搜）
        caption = None
        if image_path is not None:
            try:
                caption = image_caption(str(image_path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("图片 caption 失败 content=%s: %s", content.id, exc)
        if caption:
            content.text = caption
            _index_content(db, content, caption)
            # 以图搜图生产接线（P2-07）：caption 向量写入 image_vec 命名向量，
            # 供 POST /search/image 检索（此前生产 image_vec 恒空，以图搜图恒空结果）
            try:
                from app.services.embedding import encode_dense
                from app.services.vector_store import get_store

                img_vec = encode_dense([caption])[0]
                get_store().upsert_image_vec(
                    str(content.id),
                    img_vec,
                    payload={"text": caption, "content_type": "photo"},
                )
            except Exception as exc:  # noqa: BLE001 —— 图片向量失败不影响浏览
                logger.warning("image_vec 写入失败 content=%s: %s", content.id, exc)

        # 2. CI 打标（F1 L2 场景标签 / 搜索标签增强）
        try:
            from app.services.external.tencent_ci import image_detect_label

            if image_path is not None:
                tags = image_detect_label(str(image_path))
                if tags:
                    extra = dict(content.extra or {})
                    extra["ci_tags"] = tags
                    content.extra = extra
        except Exception as exc:  # noqa: BLE001
            logger.warning("CI 打标失败 content=%s: %s", content.id, exc)

        # Wave0 钩子：B5b 事件级敏感标记 + B1 画像标注（照片 caption/标签）
        from app.services.pipeline_ext import annotate_on_ingest, mark_sensitive_on_ingest

        mark_sensitive_on_ingest(db, content)
        annotate_on_ingest(db, content)

        # 3. 逆地理编码（高德 GPS→地名，geohash 缓存≤30 天；失败静默）
        #    contents.place 供事件聚合/搜索地点过滤/展示用元数据。
        if content.gps_lat is not None and content.gps_lng is not None and not content.place:
            try:
                from app.services.external.amap import get_place

                place = get_place(db, content.gps_lat, content.gps_lng)
                if place:
                    content.place = place
            except Exception as exc:  # noqa: BLE001 —— 逆地理失败不影响照片浏览
                logger.warning("逆地理失败 content=%s: %s", content.id, exc)
    finally:
        if tmp_file is not None:
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass


def process_content(content_id: str) -> dict:
    """内容处理主入口（RQ worker 消费；API-016 队列编排）

    返回：{"content_id", "status", "processed": [步骤], "error": 可选}
    """
    from app.services.external.asr import AsrError

    # 边界校验：content_id 必须是合法 uuid（worker 队列可能收到脏 id），
    # 否则后续 select/db.get 会抛 PG InvalidTextRepresentation（PR#1 回归修复）
    try:
        uuid.UUID(content_id)
    except (ValueError, TypeError):
        return {"content_id": content_id, "status": "not-found"}

    db: Session = SessionLocal()
    content: Content | None = None
    try:
        content = db.execute(
            select(Content).where(Content.id == content_id)
        ).scalar_one_or_none()
        if content is None:
            return {"content_id": content_id, "status": "not-found"}

        processed = []
        handler = {
            "text": _process_text,
            "voice": _process_voice,
            "photo": _process_photo,
        }.get(content.content_type)
        processing_outcome = None
        if handler:
            processing_outcome = handler(db, content)
            processed.append(content.content_type)

        # 空白语音不形成记忆事件；端侧 L0/L1 真值后，云侧只跑 L2/L3 候选。
        if processing_outcome != "no_speech":
            try:
                from app.services.events import aggregate_user

                agg = aggregate_user(db, str(content.user_id), mode="l2l3")
                if agg.get("upper_items") or agg.get("items"):
                    processed.append("events")
            except Exception as exc:  # noqa: BLE001
                logger.warning("事件聚合失败 content=%s: %s", content.id, exc)

        # 回写状态（部分步骤失败也算 done；失败明细在 extra.error）
        errors = (content.extra or {}).get("error")
        audio_processing = (content.extra or {}).get("audio_processing") or {}
        emotion_pending = (
            content.content_type == "voice"
            and audio_processing.get("emotion_enrichment") == "pending"
        )
        content.status = "done"
        db.commit()

        # 主转写先完成；本地情绪作为低优先级任务追加，不阻塞内容可用性。
        emotion_job_status = None
        if emotion_pending:
            try:
                from app.core.queue import enqueue_low

                enqueue_low(enrich_content_emotion, content_id)
                emotion_job_status = "queued"
                processed.append("emotion_queued")
            except Exception as exc:  # noqa: BLE001 -- 入队失败不否定主转写
                logger.warning(
                    "本地情绪任务入队失败 content=%s: %s",
                    content_id,
                    type(exc).__name__,
                )
                target = db.get(Content, content_id)
                if target is not None:
                    _set_emotion_enrichment(
                        target,
                        "enqueue_failed",
                        error={"code": "EMOTION_ENQUEUE_FAILED", "retryable": True},
                    )
                    db.commit()
                emotion_job_status = "enqueue_failed"
        return {
            "content_id": content_id,
            "status": "done",
            "processed": processed,
            "outcome": processing_outcome,
            "emotion_job": emotion_job_status,
            "error": errors,
        }
    except AsrError as exc:
        db.rollback()
        target = db.get(Content, content_id)
        if target is not None:
            extra = dict(target.extra or {})
            detail = exc.to_dict()
            extra["audio_processing"] = detail
            extra["error"] = detail
            target.extra = extra
            target.status = "failed"
            db.commit()
        logger.warning("process_content %s ASR 失败: %s", content_id, exc.code)
        return {
            "content_id": content_id,
            "status": "failed",
            "outcome": exc.outcome,
            "retryable": exc.retryable,
            "error": exc.code,
        }
    except Exception as exc:  # noqa: BLE001 —— 边界兜底：不伪造 done
        db.rollback()
        target = db.get(Content, content_id)
        if target is not None and target.content_type == "voice":
            detail = {
                "outcome": "failed_retryable",
                "code": "ASR_PIPELINE_ERROR",
                "message": "语音处理发生未分类异常",
                "retryable": True,
                "errors": [type(exc).__name__],
            }
            extra = dict(target.extra or {})
            extra["audio_processing"] = detail
            extra["error"] = detail
            target.extra = extra
            target.status = "failed"
            db.commit()
        logger.error("process_content %s 失败: %s", content_id, exc)
        is_voice = target is not None and target.content_type == "voice"
        return {
            "content_id": content_id,
            "status": "failed",
            "outcome": "failed_retryable" if is_voice else None,
            "retryable": is_voice,
            "error": "ASR_PIPELINE_ERROR" if is_voice else type(exc).__name__,
        }
    finally:
        db.close()
