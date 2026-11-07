"""内容 AI 管线 · 语音音频与本地情绪增强

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
本模块承载语音链的**音频落地 + 本地情绪增强**子域：

  - `_materialize_voice_audio` / `_cleanup_temporary_audio`：COS 音频 → 临时文件（及清理）
  - `_set_audio_processing` / `_set_emotion_enrichment`：转写/情绪状态位回写（extra.audio_processing）
  - `enrich_content_emotion`：低优先级 RQ 任务（只增强情绪，不改已完成的转写状态）

⚠️ 搬迁边界（不可再外移）：`_process_voice` 因调用 `_classify_content`，而后者是
`tests/test_pipeline.py` 的 monkeypatch 目标（`app.services.pipeline._classify_content`）——
patch 打在 pipeline 模块命名空间，故调用方必须与被调方同模块，`_process_voice` 留在 pipeline.py。
本模块内**不含**任何被 patch 的名字，故可安全外移。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Content
from app.db.session import SessionLocal
from app.services.pipeline_common import extra_get, logger, patch_extra


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
        filename = str(extra_get(content, "file_name") or content.cos_key)
        # 内部对象存储允许长 WAV 进入 VAD 分段；API 直传仍保持 8MB 上限。
        audio_format = validate_audio_bytes(data, filename, max_bytes=None)
        with tempfile.NamedTemporaryFile(
            suffix=temporary_suffix(audio_format), delete=False
        ) as tmp:
            tmp.write(data)
            tmp_file = Path(tmp.name)
        return tmp_file, tmp_file

    if extra_get(content, "audio_path"):
        return Path(content.extra["audio_path"]), None
    raise AsrError("AUDIO_NOT_FOUND", "语音内容缺少可处理的音频文件")


def _cleanup_temporary_audio(tmp_file: Path | None) -> None:
    if tmp_file is None:
        return
    try:
        tmp_file.unlink(missing_ok=True)
    except OSError:
        pass


def _set_audio_processing(content: Content, payload: dict) -> None:
    patch_extra(content, audio_processing=payload)
    if payload.get("outcome") in {"succeeded", "no_speech", "mock"}:
        content.extra.pop("error", None)


def _set_emotion_enrichment(
    content: Content,
    status: str,
    *,
    error: dict | None = None,
) -> None:
    detail = dict(extra_get(content, "audio_processing") or {})
    detail["emotion_enrichment"] = status
    if error is None:
        detail.pop("emotion_error", None)
    else:
        detail["emotion_error"] = error
    patch_extra(content, audio_processing=detail)


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
        detail = dict(extra_get(content, "audio_processing") or {})
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
        patch_extra(content, audio_processing=detail)
        # B5a 集成（Wave4 AgentJ 需求 4）：本地情绪增强产出真情绪后，补触发
        # 事件层联动（events.emotion）与关怀/voice_done 接线——否则初始 funasr
        # 通道恒"平静"，enrich 才产出的真情绪不会联动（幂等安全，见 emotion.py 头注）
        from app.services.pipeline_ext import consume_emotion

        consume_emotion(db, content)
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
