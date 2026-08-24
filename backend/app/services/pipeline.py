"""内容 AI 管线（P2-02 重构：从 workers/worker.py 下沉，worker 只留进程入口）

process_content(content_id)：按 content_type 分流处理，全部异步在 RQ worker 执行。
设计（与用户 2026-08-20 拍板）：
- 用户无感知：每步独立 try/except，失败不抛错不回滚，记录 extra.error
- text  → SetFit 分类 → 写 content_class/class_source/model_version → 索引
- voice → ASR 转写（真实/mock 兜底）→ 转写文本入 text → 分类 → 索引
- photo → image_caption（Qwen3-VL）写 caption 入索引 + CI 打标（失败静默）
- 全部 → 事件聚合（aggregate_user，失败静默）
- 状态机：processing → done（部分失败也算 done，失败明细在 extra.error）

依赖方向：api → services.pipeline → services.*（单向，不再反向 import worker）。
"""
from __future__ import annotations

import logging
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
    store.upsert_content(
        content_id=str(content.id),
        text=payload_text,
        dense=dense,
        sparse=sparse,
        payload={
            "content_type": content.content_type,
            "content_class": content.content_class,
            "taken_at": taken_ts,
            "user_id": str(content.user_id),
        },
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


def _process_voice(db: Session, content: Content) -> None:
    """语音：ASR 转写（真实/mock 兜底）→ 转写文本回写 + 分类 + 索引"""
    import tempfile

    from app.services.external.asr import transcribe

    # 取 COS 音频（cos_key）或本地路径（extra.audio_path，测试用）
    audio_path = None
    tmp_file = None
    if content.cos_key:
        # 生产：COS 读回二进制写系统临时文件（失败静默；用后清理）
        try:
            from app.services.external.storage import get_storage_backend

            storage = get_storage_backend()
            data = storage.get_object(content.cos_key)
            tmp_file = Path(tempfile.gettempdir()) / f"yishu_voice_{content.id}.wav"
            tmp_file.write_bytes(data)
            audio_path = tmp_file
        except Exception as exc:  # noqa: BLE001
            logger.warning("语音下载失败 content=%s: %s", content.id, exc)
    elif content.extra and content.extra.get("audio_path"):
        audio_path = Path(content.extra["audio_path"])

    if audio_path is None:
        return

    try:
        result = transcribe(str(audio_path))
        # 审查 CRITICAL 修复：mock 转写是本地兜底假文本，生产环境拒绝入库/入索引，
        # 防止假文本污染真实记忆库（此前 mock 文本会被无条件回写并向量化）。
        if result.mock and settings.app_env == "production":
            logger.warning("生产环境拒绝 mock 转写入库 content=%s（通道: %s）", content.id, result.channel)
            return
        if result.text:
            content.text = result.text
            content.emotion = {"emotion": result.emotion, "confidence": result.confidence, "channel": result.channel}
            _classify_content(db, content, result.text)
            _index_content(db, content, result.text)
    except Exception as exc:  # noqa: BLE001 —— 转写失败静默
        logger.warning("转写失败 content=%s: %s", content.id, exc)
    finally:
        if tmp_file is not None:
            try:
                tmp_file.unlink(missing_ok=True)
            except OSError:
                pass


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
    db: Session = SessionLocal()
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
        if handler:
            handler(db, content)
            processed.append(content.content_type)

        # 事件聚合（B3-6 分置：端侧 L0/L1 真值后，云侧只跑 L2/L3 候选；失败静默）
        try:
            from app.services.events import aggregate_user

            agg = aggregate_user(db, str(content.user_id), mode="l2l3")
            if agg.get("upper_items") or agg.get("items"):
                processed.append("events")
        except Exception as exc:  # noqa: BLE001
            logger.warning("事件聚合失败 content=%s: %s", content.id, exc)

        # 回写状态（部分步骤失败也算 done；失败明细在 extra.error）
        errors = (content.extra or {}).get("error")
        content.status = "done"
        db.commit()
        return {
            "content_id": content_id,
            "status": "done",
            "processed": processed,
            "error": errors,
        }
    except Exception as exc:  # noqa: BLE001 —— 边界兜底：不伪造 done
        logger.error("process_content %s 失败: %s", content_id, exc)
        return {"content_id": content_id, "status": "failed", "error": str(exc)}
    finally:
        db.close()
