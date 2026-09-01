"""媒体下发票据（Valet Key）：签发与校验（2026-08-31 新增）

## 为什么需要它

uni-app x 的 `<image :src>` **无法附加 Authorization header**，而后端
`GET /api/v1/thumbnails/{content_id}` 只认 Bearer —— 这是「历史照片在任何页面都
显示不出来」的长期根因（取证见 `_diff_ledger.md` W5 §22.3 / W6 §23.3：
全客户端 `thumbnails` 零引用、`<image :src="http...">` 零命中）。

## 业界标准解法：Valet Key（预签名 URL）

由服务端签发一枚**短时效、与对象绑定**的票据，客户端把票据放进 URL query 即可
直连下载，无需自定义 header。签名对象是**存储层票据**，不是用户 JWT —— 这与
"把 JWT 塞 query" 有本质区别：JWT 泄漏等价于账号失守，而票据泄漏只影响单个对象
且 15 分钟失效。

两种落地形态（本模块统一抽象为一层，调用方无需区分）：
  - **COS（生产）**：`CosStorageBackend.get_presigned_url`（COS 原生能力）
  - **fs / minio / fake**：HMAC-SHA256 票据 → `GET /api/v1/media/{key}`

将来 dev 切生产只需要改 `STORAGE_BACKEND=cos`，**客户端零改动**——这正是选它的
理由（另一个候选"后端代理流式"上 COS 后要么图片带宽全过服务器、要么再改一次客户端）。

## 安全性质

1. 票据 = `HMAC-SHA256(secret, "{key}\\n{exp}\\n{uid}")`，只有服务端能签发
2. 双 TTL：缩略图 24h（配合客户端本地缓存秒开）/ 原图 15m（短时效防泄露）
3. 归属二次校验：key 的用户段必须匹配票据 uid（防 A 的票据看 B 的对象）
4. URL 返回**相对路径**——后端不持有 host 配置，客户端拼自己的 BASE_URL。
   因此真机(adb reverse)/模拟器/生产域名全场景自动生效，不会 host 漂移。

## 与 /thumbnails 端点的关系

`/thumbnails/{content_id}` 保留（Windows 端/其它已接入方在用，且带懒生成兜底）。
本模块是**给移动端 `<image>` 用的补充通路**，两者共用同一份存储后端。
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from urllib.parse import quote

from app.core.config import settings

logger = logging.getLogger("yishu.media_url")

# 票据端点前缀（与 app/api/media.py 的 router prefix 一致）
MEDIA_PATH_PREFIX = "/api/v1/media/"

# key 扩展名 → Content-Type（只覆盖项目实际会存的素材类型）
_CONTENT_TYPES = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".wav": "audio/wav",
    ".mp3": "audio/mpeg",
    ".m4a": "audio/mp4",
    ".aac": "audio/aac",
}


def _secret() -> bytes:
    """签名密钥：独立配置优先，留空回退 jwt_secret（泄漏面隔离但不逼迫新部署配两套）"""
    return (settings.media_url_secret or settings.jwt_secret).encode("utf-8")


def _sign(key: str, exp: int, user_id: str) -> str:
    """HMAC-SHA256 票据（签名覆盖 key + 过期时间 + 用户，任一改动即失效）"""
    msg = f"{key}\n{exp}\n{user_id}".encode()
    return hmac.new(_secret(), msg, hashlib.sha256).hexdigest()


def build_media_path(key: str, user_id: str, ttl: int) -> str:
    """签发票据 URL（相对路径；客户端拼自己的 BASE_URL）

    Args:
        key: 存储对象键（如 `photos/{uid}/202608/xxx.jpg`）
        user_id: 归属用户（签进票据，供端点做归属二次校验）
        ttl: 有效期（秒）

    Returns:
        `/api/v1/media/{key}?exp=...&uid=...&sig=...`
    """
    exp = int(time.time()) + max(int(ttl), 1)
    sig = _sign(key, exp, user_id)
    return f"{MEDIA_PATH_PREFIX}{quote(key, safe='/')}?exp={exp}&uid={user_id}&sig={sig}"


def verify_media(key: str, exp: int, uid: str, sig: str) -> tuple[bool, str]:
    """校验票据（常量时间比较，防时序侧信道）

    Returns:
        (ok, reason)；reason 仅用于日志/脱敏响应，不回显给客户端细节。
    """
    if not key or not uid or not sig:
        return False, "missing_params"
    try:
        exp_int = int(exp)
    except (TypeError, ValueError):
        return False, "bad_exp"
    if exp_int <= 0:
        return False, "bad_exp"
    if int(time.time()) > exp_int:
        return False, "expired"
    if not hmac.compare_digest(_sign(key, exp_int, uid), sig):
        return False, "bad_sig"
    if not key_belongs_to_user(key, uid):
        return False, "forbidden"
    return True, ""


def key_belongs_to_user(key: str, user_id: str) -> bool:
    """归属校验：key 的用户段是否匹配 user_id

    key 形态（实测，见 W6 取证）：
      - `photos/{user_id}/{yyyymm}/{filename}` —— 照片/缩略图（完整 uid 段）
      - `voice/{uid前6位}/{yyyymm}/demo.wav` —— 语音（短前缀段）
      - `thumbnails/{user_id}/{yyyymm}/{filename}`

    判定：第二段 == uid，或 uid 以第二段开头（覆盖 6 位短前缀）。
    形态无法判定的历史 key（无用户段）→ 放行但记 debug 日志：
    安全性已由「票据必须服务端签发」保证，本校验是第二道防线，
    不该因为历史数据形态不一而误杀正常下发。
    """
    parts = [p for p in (key or "").split("/") if p]
    if len(parts) < 3:
        logger.debug("媒体 key 无用户段，跳过归属强校验（仅票据保护）key=%s", key)
        return True
    seg = parts[1]
    return seg == user_id or user_id.startswith(seg)


def content_type_for(key: str) -> str:
    """按扩展名推断 Content-Type（未知回落 image/jpeg —— 本项目图片以 JPEG 为主）"""
    lowered = (key or "").lower()
    for ext, ctype in _CONTENT_TYPES.items():
        if lowered.endswith(ext):
            return ctype
    return "image/jpeg"


def content_urls(
    cos_key: str | None,
    thumbnail_key: str | None,
    user_id: str | None,
) -> tuple[str | None, str | None]:
    """内容双 URL：原图（15m）+ 缩略图（24h）——"默认缩略图 + 原图按需" 契约

    Args:
        cos_key: 原件存储键
        thumbnail_key: 缩略图存储键
        user_id: 归属用户

    Returns:
        (original_url, thumbnail_url)；缺失的键对应 None。
        后端不支持签名 URL 且非 COS 时回退 None —— 调用方应继续用 /thumbnails 端点。
    """
    # 延迟导入：storage.py 会 import 本模块，避免循环依赖
    from app.services.external.storage import get_storage_backend

    uid = user_id or ""
    if not (cos_key or thumbnail_key):
        return None, None
    backend = get_storage_backend()
    original = (
        backend.get_download_url(cos_key, uid, settings.media_original_ttl)
        if cos_key
        else None
    )
    thumb = (
        backend.get_download_url(thumbnail_key, uid, settings.media_thumb_ttl)
        if thumbnail_key
        else None
    )
    return original, thumb
