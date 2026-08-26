"""缩略图管线（B4 · Wave3 AgentG · audit #1 缺口修复）

背景：`contents.thumbnail_key` 列自 Wave 0 就存在，但全仓无任何写入方
（audit_B4_sync §1）——"Windows/列表默认拉缩略图、原图按需下载"无法实现。
本模块补齐生成/存储/下发三段：

- 生成：本地 PIL 缩放（零外部依赖/零费用；COS 图片处理留作生产可选优化，
  接口与本地实现同构，换 CI 后只改本文件内部实现）
- 存储：缩略图与原件同存储后端（thumbnail_key 为对象键，确定性派生自 cos_key）
- 写入：photos 上传完成时由 upload.py/wechat service 入队 generate_thumbnail_job；
  兜底：GET 端点首次访问时按需生成（"默认拉缩略图原图按需"，懒加载语义）
- 幂等：thumbnail_key 已存在且对象可读 → 跳过；失败静默（缩略图非关键路径）

接线登记（pipeline.py 冻结只读）：pipeline._process_photo 未接缩略图——
需集成 Agent 评估是否在 pipeline_ext 或 api/contents.py 挂 generate_thumbnail_job，
本模块已提供 RQ 可入队函数，插任意照片入库点即生效。
"""
from __future__ import annotations

import io
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content
from app.db.session import SessionLocal
from app.services.external.storage import get_storage_backend

logger = logging.getLogger("yishu.thumbnails")

# 缩略图规格（MVP 定标：列表/Windows 预览 480px 足够；可调常量，不新增配置项）
THUMBNAIL_MAX_EDGE = 480
THUMBNAIL_QUALITY = 80
THUMBNAIL_CONTENT_TYPE = "image/jpeg"

# 非照片/无原件的跳过原因（测试与日志断言用）
SKIP_NOT_PHOTO = "not-photo"
SKIP_NO_ORIGINAL = "no-original"


def derive_thumbnail_key(cos_key: str) -> str:
    """从原件对象键确定性派生缩略图对象键

    cos_key "photos/<user>/<yyyymm>/<hex>_<name>.jpg"（或 wechat/...）
    → "thumbnails/<user>/<yyyymm>/<hex>_<name>.jpg"
    首段目录替换为 thumbnails/，其余路径保留（同用户目录归拢、防跨目录碰撞）。
    """
    if not cos_key or "/" not in cos_key:
        return f"thumbnails/{cos_key}"
    parts = cos_key.split("/", 1)
    return f"thumbnails/{parts[1]}"


def resize_to_jpeg(data: bytes, max_edge: int = THUMBNAIL_MAX_EDGE) -> bytes:
    """PIL 缩放 → JPEG 字节（保持宽高比，居中裁剪不做，简单缩边）

    失败（非图片/损坏/PIL 不可用）抛 ValueError——调用方决定降级策略。
    """
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        img.load()
    except Exception as exc:  # noqa: BLE001 —— 不可解码的图片字节
        raise ValueError(f"图片解码失败: {type(exc).__name__}") from exc
    if img.mode != "RGB":
        img = img.convert("RGB")
    img.thumbnail((max_edge, max_edge), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=THUMBNAIL_QUALITY, optimize=True)
    return buf.getvalue()


def generate_thumbnail(
    db: Session,
    content_id: str,
    *,
    force: bool = False,
    backend=None,
) -> dict:
    """为单条 photo 内容生成缩略图并回写 thumbnail_key（幂等）

    返回 {status: created|exists|skipped|failed, thumbnail_key?, reason?, error?}
    - 非 photo / 无 cos_key → skipped（reason 区分）
    - thumbnail_key 已有且对象可读（非 force）→ exists
    - 生成/写入失败 → failed（不抛，缩略图非关键路径）
    """
    content = db.get(Content, content_id)
    if content is None:
        return {"status": "skipped", "reason": "not-found"}
    if content.content_type != "photo":
        return {"status": "skipped", "reason": SKIP_NOT_PHOTO}
    if not content.cos_key:
        return {"status": "skipped", "reason": SKIP_NO_ORIGINAL}

    storage = backend or get_storage_backend()
    thumbnail_key = content.thumbnail_key or derive_thumbnail_key(content.cos_key)
    if not force and content.thumbnail_key:
        try:
            if storage.object_exists(thumbnail_key):
                return {"status": "exists", "thumbnail_key": thumbnail_key}
        except Exception as exc:  # noqa: BLE001 —— 后端探测失败继续生成
            logger.warning("缩略图存在性探测失败 content=%s: %s", content_id, exc)

    try:
        original = storage.get_object(content.cos_key)
        thumb = resize_to_jpeg(original)
        storage.put_object(thumbnail_key, thumb)
    except KeyError as exc:
        logger.warning("缩略图生成失败（原件缺失）content=%s: %s", content_id, exc)
        return {"status": "failed", "error": "original-missing"}
    except Exception as exc:  # noqa: BLE001 —— 解码/写入失败静默
        logger.warning("缩略图生成失败 content=%s: %s", content_id, exc)
        return {"status": "failed", "error": type(exc).__name__}

    if content.thumbnail_key != thumbnail_key:
        content.thumbnail_key = thumbnail_key
        db.commit()
    return {"status": "created", "thumbnail_key": thumbnail_key}


def generate_thumbnail_job(content_id: str) -> dict:
    """RQ worker 入口：独立 Session，失败不抛（缩略图非关键路径）

    upload.py / wechat service 在照片入库后 enqueue_high 本函数；
    也可由 backfill_thumbnails.py 批量调用。
    """
    db: Session = SessionLocal()
    try:
        return generate_thumbnail(db, content_id)
    except Exception as exc:  # noqa: BLE001 —— 边界兜底
        logger.error("generate_thumbnail_job 异常 content=%s: %s", content_id, exc)
        return {"status": "failed", "error": type(exc).__name__}
    finally:
        db.close()


def get_thumbnail_bytes(db: Session, content_id: str, user_id: str) -> tuple[bytes, str]:
    """下发缩略图：归属校验 → 缺则按需生成 → 读对象返回 (bytes, content_type)

    - 内容不存在 / 非本人 / 非照片 / 无法生成 → KeyError（API 层转 404）
    - 懒生成失败 → KeyError（客户端可回退原图，见 api/thumbnails 契约）
    """
    content = db.get(Content, content_id)
    if content is None or str(content.user_id) != str(user_id):
        raise KeyError(f"内容不存在: {content_id}")
    if content.content_type != "photo":
        raise KeyError(f"内容非照片，无缩略图: {content_id}")

    result = generate_thumbnail(db, content_id)
    if result["status"] in ("skipped", "failed"):
        raise KeyError(f"缩略图不可用: {content_id}")

    storage = get_storage_backend()
    try:
        data = storage.get_object(result["thumbnail_key"])
    except KeyError as exc:
        raise KeyError(f"缩略图对象缺失: {result['thumbnail_key']}") from exc
    return data, THUMBNAIL_CONTENT_TYPE


def list_photos_without_thumbnail(db: Session, limit: int = 200) -> list[Content]:
    """回填扫描：photo 且 cos_key 非空但 thumbnail_key 为空的记录（backfill 脚本用）"""
    rows = db.scalars(
        select(Content)
        .where(
            Content.content_type == "photo",
            Content.cos_key.is_not(None),
            Content.thumbnail_key.is_(None),
        )
        .order_by(Content.created_at)
        .limit(limit)
    ).all()
    return list(rows)
