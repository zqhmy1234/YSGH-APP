"""内容 AI 管线（P2-02 重构：从 workers/worker.py 下沉，worker 只留进程入口）

process_content(content_id)：按 content_type 分流处理，全部异步在 RQ worker 执行。
设计：
- 非关键增强步骤失败可静默降级；ASR 主步骤失败必须显式回写 failed
- A2（P0-2）：retryable 失败（网络抖动等）先落审计再 re-raise → RQ Retry(3)
  真正重投；非 retryable（终态）才吞掉返回 failed。RQ 重投耗尽仍失败 →
  workers/requeue_job.py 超龄重扫兜底（含 P0-4 遗留的 processing 卡死）
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


def patch_extra(content: Content, **updates) -> None:
    """extra JSON 列拷贝-合并-回写样板收敛（TD-P2B · S1-M6：原 7 处内联
    `extra = dict(content.extra or {}); extra[...] = ...; content.extra = extra`）"""
    extra = dict(content.extra or {})
    extra.update(updates)
    content.extra = extra


def extra_get(content: Content, key: str, default=None):
    """读取侧样板收敛：`(content.extra or {}).get(key, default)`"""
    return (content.extra or {}).get(key, default)


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
        # Wave0 钩子：B5b 敏感标记 + B1 画像标注
        # （Qdrant 索引 R2#1 后置到主提交之后，见 _index_after_commit）
        from app.services.pipeline_ext import annotate_on_ingest, mark_sensitive_on_ingest

        mark_sensitive_on_ingest(db, content)
        annotate_on_ingest(db, content)


def _set_audio_processing(content: Content, payload: dict) -> None:
    patch_extra(content, audio_processing=payload)
    if payload.get("outcome") in {"succeeded", "no_speech", "mock"}:
        content.extra.pop("error", None)


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
        # Qdrant 索引 R2#1 后置到主提交之后（_index_after_commit）——索引失败
        # 不再回写 index_error（仅日志），内容已提交 done，不影响可搜索性兜底
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
    elif extra_get(content, "image_path"):
        image_path = Path(content.extra["image_path"])

    try:
        # 1. caption（图片塔；失败不影响照片浏览，仅不可搜）
        #    Qdrant 索引（text_vec/image_vec）+ payload 补全 R2#1 后置到主提交
        #    之后（_index_after_commit，此时 place/ci_tags 已就绪，无需 update_payload）
        caption = None
        if image_path is not None:
            try:
                caption = image_caption(str(image_path))
            except Exception as exc:  # noqa: BLE001
                logger.warning("图片 caption 失败 content=%s: %s", content.id, exc)
        if caption:
            content.text = caption

        # 2. CI 打标（F1 L2 场景标签 / 搜索标签增强）
        # 2026-08-26 真实 key 验证修复：CI 打标要求图片在 COS（image_key=COS key），
        # fs 真实模式的本地路径不是 COS key → NoSuchKey 静默失效。
        # 条件：cos 后端（真实打标）或 mock 模式（测试/沙箱，monkeypatch 或 mock 契约）才调用；
        # fs 真实模式跳过（放行+日志），STORAGE_BACKEND=cos 上线后自动启用。
        if settings.storage_backend == "cos" or settings.mock_external_ai:
            try:
                from app.services.external.tencent_ci import image_detect_label

                if image_path is not None:
                    tags = image_detect_label(str(image_path))
                    if tags:
                        patch_extra(content, ci_tags=tags)
            except Exception as exc:  # noqa: BLE001
                logger.warning("CI 打标失败 content=%s: %s", content.id, exc)
        elif image_path is not None:
            logger.info("CI 打标跳过（STORAGE_BACKEND=%s 且非 mock，图片不在 COS）", settings.storage_backend)

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


def _index_after_commit(db: Session, content: Content) -> None:
    """主提交后的 Qdrant 索引（R2#1：Qdrant 写后置到 DB 提交之后）

    全部向量写入移到主 commit 之后——DB 是内容状态真值，Qdrant 是事后
    尽力而为的检索增强：主提交失败则根本不写向量（无孤儿向量）；索引失败
    只影响可搜索性，不否定已提交的 done 状态。upsert 按 content_id 幂等
    （UUID5 稳定同点 + 整点替换），RQ 重投/超龄重扫重跑安全。
    photo 附带 image_vec（以图搜图）写入；place/ci_tags 此时已就绪，
    由 extend_payload 直接进 payload（不再需要 update_payload 补全）。
    """
    text = content.text
    if not text:
        return
    _index_content(db, content, text)
    if content.content_type != "photo":
        return
    try:
        from app.services.embedding import encode_dense
        from app.services.vector_store import get_store

        img_vec = encode_dense([text])[0]
        get_store().upsert_image_vec(
            str(content.id),
            img_vec,
            payload={"text": text, "content_type": "photo"},
        )
    except Exception as exc:  # noqa: BLE001 —— 图片向量失败不影响浏览
        logger.warning("image_vec 写入失败 content=%s: %s", content.id, exc)


def _enqueue_user_aggregation(user_id: str) -> str:
    """F3/R5-3：按 user 级 key 入队聚合独立 RQ 任务（同用户同时多内容只跑一次）

    core/queue.enqueue_unique 的 Redis SETNX 原子预占位做去重合并：
      - job_id = run_user_aggregation_user_<uid>（确定性，同用户并发/重复触发不重复入队）
      - 聚合任务扫描该用户全部未成候选内容（含本批并发内容），一次覆盖并发批次
      - 放 low 队列（P2-P4 聚合/批量），DEFAULT_JOB_TIMEOUT（300s）

    返回 "queued"（已入队）/ "enqueue_failed"（入队失败，调用方只记日志不否定主结果）。
    """
    try:
        from app.core.queue import DEFAULT_JOB_TIMEOUT, QUEUE_LOW, enqueue_unique
        from app.services.events import run_user_aggregation

        enqueue_unique(
            run_user_aggregation,
            f"user:{user_id}",
            str(user_id),
            mode="l2l3",
            queue_name=QUEUE_LOW,
            job_timeout=DEFAULT_JOB_TIMEOUT,
        )
        return "queued"
    except Exception as exc:  # noqa: BLE001 —— 入队失败不影响主转写结果
        logger.warning("聚合任务入队失败 user=%s: %s", user_id, type(exc).__name__)
        return "enqueue_failed"


def process_content(content_id: str) -> dict:
    """内容处理主入口（RQ worker 消费；API-016 队列编排）

    返回：{"content_id", "status", "processed": [步骤], "error": 可选}

    R2#1（分阶段提交，重构侦察 R2-P1#1）：
      - 阶段 0：加载后先提交"processing"状态位（结束当前事务）——COS 下载 /
        ASR 转写 / dashscope / 腾讯 CI / 高德等分钟级外部调用全部在事务外进行，
        contents 行锁/连接不再横跨整个管线（ASR/LLM 调用移出行锁窗口）
      - 阶段 1：处理器（外调 + 内存变更）包 begin_nested（SAVEPOINT）失败隔离
      - 阶段 2：事件聚合——F3/R5-3 已拆出为独立 per-user RQ 任务（主提交后经
        enqueue_unique 按 user 级 key 入队去重合并，不再同步跑聚合写库）
      - 阶段 3：主提交 status=done + 全部 DB 状态变更一次落库
      - 阶段 4：Qdrant 索引后置到主提交之后（DB 是真值，Qdrant 事后幂等增强；
        主提交失败则无向量写入，无孤儿向量；重投重跑按 content_id 幂等）
      - 阶段 5：本地情绪低优先级任务入队——F4/R5-5 尾段**先入队后提交**（done
        主提交前入队，消除 commit→enqueue 间隙崩溃丢任务；enqueue_unique 同
        content 键不重复入队），入队失败回写 enqueue_failed 审计标记
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

        # 阶段 0：外部调用前提交状态位（分阶段提交）——"processing"先落库并
        # 结束当前事务，后续分钟级外部调用不持有 contents 行锁/数据库连接
        if content.status != "processing":
            content.status = "processing"
        db.commit()

        processed = []
        handler = {
            "text": _process_text,
            "voice": _process_voice,
            "photo": _process_photo,
        }.get(content.content_type)
        processing_outcome = None
        if handler:
            # 阶段 1：处理器（SAVEPOINT 失败隔离——处理器部分 DB 变更失败仅回滚
            # 到本保存点，由外层统一回写 failed / re-raise，不外泄部分写入）
            with db.begin_nested():
                processing_outcome = handler(db, content)
            processed.append(content.content_type)

        # 空白语音不形成记忆事件；端侧 L0/L1 真值后，云侧只跑 L2/L3 候选。
        # F3/R5-3：聚合从 process_content 拆出为独立 per-user RQ 任务——
        # 入队延后到主提交后执行（聚合任务独立会话须能读到已提交内容），
        # enqueue_unique 按 user 级 key 去重合并（同用户同时多内容只跑一次）。
        agg_pending = processing_outcome != "no_speech"

        # 回写状态（部分步骤失败也算 done；失败明细在 extra.error）
        # A2（P0-2）：本轮全成功则清掉上一轮失败残留的 error 标记——retryable
        # 失败 re-raise 重投成功后 status 由 failed → done，陈旧 error 会让 done
        # 内容误显示失败（error 仅由顶层失败处理器写入，此处可安全清除）。
        # 注意：JSONB 列必须整体重赋（content.extra = new）才能被 ORM 追踪，
        # 原地 pop 不会触发 dirty 检测、commit 不会落库。
        if content.extra and "error" in content.extra:
            extra = dict(content.extra)
            extra.pop("error", None)
            content.extra = extra
        errors = extra_get(content, "error")
        audio_processing = extra_get(content, "audio_processing") or {}
        emotion_pending = (
            content.content_type == "voice"
            and audio_processing.get("emotion_enrichment") == "pending"
        )
        # F4/R5-5（重构侦察 R5-5）：尾段先入队后提交——情绪任务在 done 主提交前
        # 入队，消除 commit→enqueue 间隙崩溃丢任务；enqueue_unique 同 content 键
        # 不重复入队（双 process_content 并发只投一次情绪任务，防双推理双副作用）。
        emotion_job_status = None
        if emotion_pending:
            try:
                from app.core.queue import DEFAULT_JOB_TIMEOUT, QUEUE_LOW, enqueue_unique

                enqueue_unique(
                    enrich_content_emotion,
                    content_id,
                    queue_name=QUEUE_LOW,
                    job_timeout=DEFAULT_JOB_TIMEOUT,
                )
                emotion_job_status = "queued"
                processed.append("emotion_queued")
            except Exception as exc:  # noqa: BLE001 -- 入队失败不否定主转写
                logger.warning(
                    "本地情绪任务入队失败 content=%s: %s",
                    content_id,
                    type(exc).__name__,
                )
                # 失败不丢任务：回写 enqueue_failed 审计标记（requeue_job 超龄
                # 重扫兜底，见 workers/requeue_job.py），主转写仍落 done
                _set_emotion_enrichment(
                    content,
                    "enqueue_failed",
                    error={"code": "EMOTION_ENQUEUE_FAILED", "retryable": True},
                )
                emotion_job_status = "enqueue_failed"

        content.status = "done"
        # 阶段 3：主提交（status=done 与全部 DB 状态变更一次落库）
        db.commit()

        # F3/R5-3：聚合独立任务入队（主提交后——聚合任务独立会话须见已提交内容）。
        # enqueue_unique 按 user 级 key 去重：同用户并发/连续多内容只投一个聚合任务
        # （聚合扫描该用户全部未成候选内容，一次覆盖并发批次）；入队失败仅记日志
        # 与返回标记，不影响主转写结果（聚合失败静默语义，requeue_job 兜底扫描）。
        agg_job_status = None
        if agg_pending:
            agg_job_status = _enqueue_user_aggregation(str(content.user_id))
            if agg_job_status == "queued":
                processed.append("events_queued")
            else:
                processed.append("events_enqueue_failed")

        # 阶段 4：Qdrant 后置索引（失败只影响可搜索性，不否定已提交的处理）
        try:
            _index_after_commit(db, content)
        except Exception as exc:  # noqa: BLE001
            logger.warning("后置索引失败 content=%s: %s", content.id, exc)

        return {
            "content_id": content_id,
            "status": "done",
            "processed": processed,
            "outcome": processing_outcome,
            "emotion_job": emotion_job_status,
            "agg_job": agg_job_status,
            "error": errors,
        }
    except AsrError as exc:
        db.rollback()
        target = db.get(Content, content_id)
        if target is not None:
            detail = exc.to_dict()
            patch_extra(target, audio_processing=detail, error=detail)
            target.status = "failed"
            db.commit()
        logger.warning("process_content %s ASR 失败: %s", content_id, exc.code)
        if exc.retryable:
            # A2（P0-2）：retryable 错误先落审计（failed + 明细）再 re-raise，
            # 由 RQ Retry(3) 真正重投（10s→30s→90s 指数退避）。此前吞掉正常
            # 返回 → RQ 视为成功不重试，网络抖动后内容永久 failed，语音静默
            # 丢失。RQ 重投耗尽仍失败 → requeue_job 超龄重扫兜底。
            raise
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
        # P0-4（审查 H-4）：text/photo 同样回写 failed + extra.error——此前仅 voice
        # 回写，其余类型意外异常后永久卡 processing（RQ 视为成功不重试，静默坏死）。
        # A2（P0-2）：回写审计后 re-raise → RQ Retry(3) 真正重投（10s→30s→90s）；
        # RQ 重投耗尽仍失败 → workers/requeue_job.py 超龄重扫兜底（P0-4 遗留闭环：
        # 修复上线前产生的历史 failed/processing 卡死记录由该 job 统一重投/置终态）。
        if target is not None:
            is_voice = target.content_type == "voice"
            detail = {
                "outcome": "failed_retryable",
                "code": "ASR_PIPELINE_ERROR" if is_voice else "PIPELINE_ERROR",
                "message": (
                    "语音处理发生未分类异常" if is_voice
                    else f"{target.content_type} 处理发生未分类异常"
                ),
                "retryable": True,
                "errors": [type(exc).__name__],
            }
            if is_voice:
                patch_extra(target, audio_processing=detail, error=detail)
            else:
                patch_extra(target, error=detail)
            target.status = "failed"
            db.commit()
        logger.error("process_content %s 失败: %s", content_id, exc)
        # A2（P0-2）：审计已落库，re-raise 交 RQ Retry(3) 重投——此前吞掉
        # 正常返回导致 RQ 视为成功，未分类异常内容静默卡 failed/processing。
        raise
    finally:
        db.close()
