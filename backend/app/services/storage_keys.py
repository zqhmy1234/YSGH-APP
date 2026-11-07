r"""存储对象键**命名空间**的唯一来源（D02-2 / D09-2 · 功能修复波 簇③）。

## 原状（缺陷）

「键前缀白名单」在 **5 处各写一份字面量**，且各自维护：

| # | 位置 | 字面量 |
|---|---|---|
| 1 | `api/media.py::_ALLOWED_PREFIXES` | `("photos/", "voice/", "thumbnails/")` |
| 2 | `schemas/content.py::_STORAGE_KEY_PREFIX` | `^(photos\|voice\|thumbnails)/` |
| 3 | `api/contents/__init__.py::_validate_cos_key` | `f"photos/{uid}/"` 等三条 |
| 4 | `services/external/storage.py::_build_sts_policy` | `("photos", "voice", "thumbnails")` |
| 5 | `services/wechat/service.py::_process_media` | 直接拼 `f"wechat/{uid}/..."` |

后果（**P0**）：微信原件落在 `wechat/{uid}/` 命名空间（第 5 处），而**第 1 处白名单
不含该前缀** ⇒ 票据签发成功（`media_url.build_media_path`）、`key_belongs_to_user`
也判归属通过，但 `GET /api/v1/media/{key}` 在**白名单检查**处恒返回 401 MEDIA_001
⇒ 微信图片/语音原件**永远下发不出来**（缩略图不受影响，因为 `thumbnails/` 在白名单内）。

## 现口径（唯一来源）

- **UPLOAD_NAMESPACES** —— 客户端可**直传**的命名空间（STS policy 只签这几个）：
  `photos` / `voice` / `thumbnails`。它们同时也是内容表 `cos_key` 的合法前缀。
- **SERVER_NAMESPACES** —— **仅服务端写入**的命名空间（客户端不可直传）：
  `wechat`（企微媒体原件）。
- **MEDIA_NAMESPACES** = 两者并集 —— **票据下发白名单**（`/media/{key}`）。

新增一个命名空间 ⇒ 只改本文件一处；各站点均从此派生（`test_storage_keys_single_source.py`
对派生关系与"白名单不含 wechat"这条回归做断言）。
"""
from __future__ import annotations

# 客户端可直传（STS policy 签发范围）；内容表 cos_key 合法前缀
UPLOAD_NAMESPACES: tuple[str, ...] = ("photos", "voice", "thumbnails")

# 仅服务端写入（客户端不可直传）：企微媒体原件
SERVER_NAMESPACES: tuple[str, ...] = ("wechat",)

# 票据下发白名单（= 可下发命名空间的并集）
MEDIA_NAMESPACES: tuple[str, ...] = UPLOAD_NAMESPACES + SERVER_NAMESPACES

# 内容表 cos_key 合法前缀（含服务端写入命名空间：微信链路建 Content 时用的就是 wechat/ 键）
CONTENT_NAMESPACES: tuple[str, ...] = UPLOAD_NAMESPACES + SERVER_NAMESPACES

# 票据下发白名单的 "ns/" 形态（供 startswith 使用）
MEDIA_PREFIXES: tuple[str, ...] = tuple(f"{ns}/" for ns in MEDIA_NAMESPACES)

# 内容表 cos_key 校验的 pydantic pattern（`^(photos|voice|thumbnails|wechat)/`）
CONTENT_KEY_PATTERN: str = r"^(" + "|".join(CONTENT_NAMESPACES) + r")/"


def is_media_key_allowed(key: str | None) -> bool:
    """键是否在**票据下发白名单**内（`/media/{key}` 的准入判定）。

    只做前缀判定，不判归属/票据——两者分别由 `media_url.verify_media` 与
    存储后端负责。空键一律拒。
    """
    if not key:
        return False
    return key.startswith(MEDIA_PREFIXES)


def user_scoped_prefixes(user_id: str) -> tuple[str, ...]:
    """当前用户在各命名空间下的前缀（`photos/{uid}/` …），供内容 `cos_key` 归属校验。

    与 `is_media_key_allowed` 的差别：这里**必须带用户段**（防跨租户）。
    """
    return tuple(f"{ns}/{user_id}/" for ns in CONTENT_NAMESPACES)


def wechat_object_key(user_id: str, msg_id: str, extension: str) -> str:
    """企微媒体原件对象键（**唯一布局**）：`wechat/{uid}/{msg_id}{ext}`。

    与 `media_keys.photo_object_key` 同族（命名空间 + 用户段 + 文件名），
    但微信侧原文无扩展名、由 `_media_extension` 按消息类型补。`extension`
    不带点也一并归一（`.amr` / `amr` 均可）。
    """
    ext = extension if extension.startswith(".") else f".{extension}"
    return f"wechat/{user_id}/{msg_id}{ext}"
