"""媒体票据下发端点（Valet Key · 2026-08-31 新增）

## 它解决什么

`GET /api/v1/thumbnails/{content_id}` 只认 Authorization Bearer，但 uni-app x 的
`<image :src>` **发不出自定义 header** —— 移动端历史照片因此永远显示不出来。
本端点提供一条「URL 自证」通路：票据（exp + uid + sig）在 query 里，
`<image :src="BASE + url">` 可以直接加载，无需任何 header。

## 安全模型

| 层 | 机制 |
| --- | --- |
| 签发 | HMAC-SHA256(secret, key\\nexp\\nuid)，只有服务端能造 |
| 时效 | 缩略图 24h / 原图 15m（config.media_*_ttl） |
| 归属 | key 用户段必须匹配票据 uid（media_url.key_belongs_to_user） |
| 前缀 | 只允许 photos/ voice/ thumbnails/ 三类键 |
| 路径 | 拒绝 `..` / 绝对路径 / 反斜杠（storage._safe_path，防目录遍历） |

⚠️ **本端点刻意不挂 `get_current_user`** —— 票据即凭证，这是 Valet Key 的定义。
（若要求 Bearer，则 `<image>` 依然加载不了，本端点就失去存在意义。）

## 与 /thumbnails 的分工

- `/thumbnails/{content_id}`：Bearer + 懒生成兜底，Windows 端/其它已接入方继续用
- `/media/{key}`：票据直连，移动端 `<image>` 用；不做懒生成（缩略图应在入库时生成）

将来切 COS 生产后端后，`get_download_url` 直接返回 COS 原生 presigned URL，
**不再经过本端点**（客户端零改动）——本端点仅服务 fs/minio/fake 后端。
"""
import logging

from fastapi import Depends
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user, uuid4_str
from app.core.errors import ERR_MEDIA_001, ERR_MEDIA_002, ERR_MEDIA_003, ApiError
from app.db.models import Content, User
from app.db.session import get_db
from app.services.external.media_url import content_type_for, verify_media
from app.services.external.storage import StorageError, get_storage_backend

logger = logging.getLogger("yishu.media")

router = make_router(prefix="/api/v1/media", tags=["media"])

# 允许下发的键前缀（与 schemas/content.py 的 _STORAGE_KEY_PREFIX 同源）
_ALLOWED_PREFIXES = ("photos/", "voice/", "thumbnails/")


@router.get("/audio/{content_id}")
def get_content_audio(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """按 content_id 下发语音字节（Bearer 鉴权 · W0-1 2026-09-02 新增）

    契约（F5 组拍板「本组补端点」，供客户端 uni.downloadFile 带 header 就地播放）：
    - Bearer + content_id 定位（区别于上方票据直连的 /{key:path}）——token 只能走
      header（api.uts:57），<audio>/<image> 带不上，故语音播放必须走本端点
    - 归属校验：他人内容/不存在 → 404 MEDIA_003（与 thumbnails 同口径，防 IDOR）
    - 非语音内容或 cos_key 缺失 → 404 MEDIA_003
    - Content-Type 按存储键扩展名（wav/m4a/aac 已在 media_url._CONTENT_TYPES）；
      未知扩展名回落 application/octet-stream（不用 image/jpeg 兜底——那是图片表的语义）
    - Cache-Control: private, max-age=86400（语音入库后字节不变，可私有缓存）
    - 整文件直出（语音条 ≤60s，体积小）；真流式/Range 待 COS 后端切换时一并做

    ⚠️ 路由声明顺序：必须位于 /{key:path} 兜底路由**之前**，否则被兜底捕获后
    因前缀白名单外的「audio/」键 401。
    """
    cid = uuid4_str(content_id)
    content = db.scalar(select(Content).where(Content.id == cid))
    if (
        content is None
        or content.user_id != user.id
        or content.content_type != "voice"
        or not content.cos_key
    ):
        # 不区分「不存在/非本人/非语音」——统一 404，避免向调用方泄漏内容归属信息
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404)

    key = content.cos_key
    backend = get_storage_backend()
    try:
        data = backend.get_object(key)
    except (KeyError, ValueError) as exc:
        logger.info("音频对象不可用 key=%s err=%s", key, exc)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc
    except StorageError as exc:
        logger.warning("音频读取失败（存储故障）key=%s code=%s", key, exc.code)
        raise ApiError(ERR_MEDIA_003, "音频不可用", http=404) from exc

    ctype = content_type_for(key)
    if not ctype.startswith("audio/"):
        ctype = "application/octet-stream"
    return Response(
        content=data,
        media_type=ctype,
        headers={
            "Cache-Control": "private, max-age=86400",
            "Accept-Ranges": "none",
        },
    )


@router.get("/{key:path}")
def get_media(key: str, exp: int | None = None, uid: str | None = None, sig: str | None = None):
    """按票据下发媒体字节（无 Bearer；票据自证）

    失败一律 401 MEDIA_001（不区分"过期"还是"签名错"——避免给攻击者反馈
    有效的 key + 过期时间组合，属 standard practice）。仅当票据有效但对象缺失时 404。
    """
    # 前缀白名单：即便签名有效，也不允许下发白名单外的键（防越权读其它命名空间）
    if not key or not key.startswith(_ALLOWED_PREFIXES):
        raise ApiError(ERR_MEDIA_001, "媒体票据无效或已过期", http=401)

    ok, reason = verify_media(key, exp or 0, uid or "", sig or "")
    if not ok:
        logger.info("媒体票据校验失败 reason=%s key=%s uid=%s", reason, key, uid)
        raise ApiError(ERR_MEDIA_001, "媒体票据无效或已过期", http=401)

    backend = get_storage_backend()
    try:
        data = backend.get_object(key)
    except (KeyError, ValueError) as exc:
        # ValueError = 非法键（目录遍历尝试）；对外统一 404，不回显原因
        logger.info("媒体对象不可用 key=%s err=%s", key, exc)
        raise ApiError(ERR_MEDIA_002, "媒体对象不存在", http=404) from exc
    except StorageError as exc:
        logger.warning("媒体读取失败（存储故障）key=%s code=%s", key, exc.code)
        raise ApiError(ERR_MEDIA_002, "媒体对象不存在", http=404) from exc

    # private：URL 含票据，不应进共享缓存/CDN；max-age 取票据剩余秒数
    remaining = max((exp or 0) - _now(), 0)
    return Response(
        content=data,
        media_type=content_type_for(key),
        headers={
            "Cache-Control": f"private, max-age={remaining}",
            "Accept-Ranges": "none",
        },
    )


def _now() -> int:
    import time

    return int(time.time())
