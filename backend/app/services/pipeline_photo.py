"""内容 AI 管线 · 照片处理器（image_caption / CI 打标 / 逆地理）

波D ①（2026-09-10）自 `pipeline.py` 逐字拆出（**只搬代码不改逻辑**）：
`_process_photo` 是 CONTENT_HANDLERS 注册的内容类型处理器，由 `_resolve_handler`
在 pipeline 模块命名空间按名解析（`globals().get(name)`）——因此 pipeline.py 需
从本模块 re-export 该函数，`tests/test_pipeline.py` 的
`monkeypatch.setattr("app.services.pipeline._process_photo", boom)` 才能命中。

⚠️ 本函数体**不调用**任何被 patch 的名字（仅用 `extra_get` / `patch_extra` 样板），
故可安全外移；`_process_voice` 因调用被 patch 的 `_classify_content` 而必须留在 pipeline.py。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import Content
from app.services.pipeline_common import extra_get, logger, patch_extra


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

        # BA3（AI 链批）挂钩：照片 AI 一句话描述（qwen3-vl，复用 dashscope 封装）写
        # ai_description。在此内联执行（本地临时图片文件仅存活于本处理器窗口）。
        # 失败语义同 caption：仅 log，不阻断照片主流程（打标是增强项，主内容仍 done）；
        # LLM 未配置走「配置缺失」显式路径（AiTaggingError.LLM_NOT_CONFIGURED，log 可查）。
        if image_path is not None:
            try:
                from app.services.ai_tagging import generate_photo_description

                content.ai_description = generate_photo_description(
                    content, image_path=str(image_path)
                )
                logger.info("照片 AI 描述成功 content=%s", content.id)
            except Exception as exc:  # noqa: BLE001 —— 描述失败不影响照片浏览
                logger.warning("照片 AI 描述失败 content=%s: %s", content.id, exc)

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
