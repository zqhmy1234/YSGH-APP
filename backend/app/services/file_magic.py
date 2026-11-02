"""文件魔数嗅探（P0-3 · 审查 H3 上传内容嗅探）

背景：照片上传链路此前只按扩展名/content_type 头判定，`.jpg` 文件名 + 任意字节
即可通过（HTML/脚本/畸形图片投毒）。ASR 链路已有 `_matches_magic` 先例
（app/services/external/asr.py），本模块抽出照片侧公共 helper，供
api/contents.py upload_photo 与 services/upload.py register_photo_content 复用。

支持格式（与 ALLOWED_PHOTO_EXTS 对齐）：jpeg / png / webp / heic(heif)。
仅检查文件头魔数，不做完整解码（解码防护见 thumbnails.resize_to_jpeg 的
Image.MAX_IMAGE_PIXELS）。
"""
from __future__ import annotations

# 各格式魔数签名（取文件头前 16 字节判断）
_JPEG_MAGIC = b"\xff\xd8\xff"
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# HEIF/AVIF 家族 brand（ftyp 后的四字符，HEIC 常见 heic/heix/hevc/hevx/mif1/msf1）
_HEIF_BRANDS = {b"heic", b"heix", b"hevc", b"hevx", b"mif1", b"msf1", b"avif"}

# 支持的格式名（测试与日志断言用）
FORMAT_JPEG = "jpeg"
FORMAT_PNG = "png"
FORMAT_WEBP = "webp"
FORMAT_HEIC = "heic"


def detect_photo_format(data: bytes) -> str | None:
    """按魔数识别照片格式；无法识别（非照片/伪装文件）返回 None"""
    head = data[:16]
    if head.startswith(_JPEG_MAGIC):
        return FORMAT_JPEG
    if data.startswith(_PNG_MAGIC):
        return FORMAT_PNG
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return FORMAT_WEBP
    if len(data) >= 12 and data[4:8] == b"ftyp" and data[8:12] in _HEIF_BRANDS:
        return FORMAT_HEIC
    return None


def is_photo_bytes(data: bytes) -> bool:
    """是否为合法照片字节（魔数校验；扩展名/content_type 均不可信）"""
    return detect_photo_format(data) is not None
