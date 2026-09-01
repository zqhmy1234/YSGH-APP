"""upload_meta.py —— 照片上传元数据校验统一入口（TD-P2B · S1-H2 收口）

contents.py（API 层）与 upload.py（service 层）此前各写一份 meta 校验
（meta JSON 解析 / taken_at ISO / gps 边界 / source 白名单），字段契约靠注释对齐、
常量也各自定义（_PHOTO_SOURCES vs 内联元组、MAX_PHOTO_BYTES/ALLOWED_PHOTO_EXTS 仅
contents.py 有一份）——两条上传链路（multipart 中转 vs 分片 complete）改一处漏一处。

现统一本模块：
- PhotoMeta dataclass：taken_at（aware datetime）/ gps_lat / gps_lng / source
- parse_photo_meta(meta)：meta JSON 串 → PhotoMeta，校验失败抛 MetaValidationError
  （异常转译由调用方负责：API 层捕获 → ApiError("CONTENT_005")；service 层 → ValueError）
- 常量 PHOTO_SOURCES / MAX_PHOTO_BYTES / ALLOWED_PHOTO_EXTS 一并收敛到此

time 解析复用 sync_common.parse_ts（naive 视为 UTC 防 DB 比较 TypeError，S1-M1 收口）。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime

from app.services.sync_common import parse_ts

# 照片上传共享常量（原 contents.py:60-64 定义、upload.py:256 内联元组——双份漂移源）
MAX_PHOTO_BYTES = 20 * 1024 * 1024  # 单张 20MB（API multipart 中转上限）
ALLOWED_PHOTO_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif"}
PHOTO_SOURCES = ("app", "windows", "wechat", "import")


class MetaValidationError(ValueError):
    """meta 校验失败（继承 ValueError：service 层可直接透传语义；API 层按需转 ApiError）"""


@dataclass
class PhotoMeta:
    taken_at: datetime | None
    gps_lat: float | None
    gps_lng: float | None
    source: str = "app"
    # 原始 meta 对象（非共享字段如 upload_mode/on_wifi/extra/content_id 由调用方读取）
    raw: dict = field(default_factory=dict)


def parse_photo_meta(meta: str) -> PhotoMeta:
    """meta JSON 串 → PhotoMeta（JSON 结构 + taken_at ISO + gps 边界 + source 白名单）

    - meta 空串 → 默认空元数据（taken_at/gps 为 None，source="app"）
    - 结构非法（非 JSON / 非对象）→ MetaValidationError("meta 必须…")
    - taken_at 提供但非 ISO8601 → MetaValidationError；naive 时间按 UTC 解释
    - gps 非数值 / 越界 → MetaValidationError
    - source 不在白名单 → MetaValidationError
    """
    try:
        meta_obj = json.loads(meta) if meta.strip() else {}
        if not isinstance(meta_obj, dict):
            raise MetaValidationError("meta 必须是 JSON 对象")
    except json.JSONDecodeError as exc:
        raise MetaValidationError("meta 必须为合法 JSON 对象") from exc

    taken_at = None
    if meta_obj.get("taken_at"):
        taken_at = parse_ts(str(meta_obj["taken_at"]))
        if taken_at is None:
            raise MetaValidationError("taken_at 格式无效（ISO8601）")

    gps_lat = meta_obj.get("gps_lat")
    gps_lng = meta_obj.get("gps_lng")
    try:
        gps_lat = float(gps_lat) if gps_lat is not None else None
        gps_lng = float(gps_lng) if gps_lng is not None else None
    except (TypeError, ValueError) as exc:
        raise MetaValidationError("gps_lat/gps_lng 必须为数值") from exc
    if gps_lat is not None and not (-90 <= gps_lat <= 90):
        raise MetaValidationError("gps_lat 越界（-90~90）")
    if gps_lng is not None and not (-180 <= gps_lng <= 180):
        raise MetaValidationError("gps_lng 越界（-180~180）")

    source = meta_obj.get("source", "app")
    if source not in PHOTO_SOURCES:
        raise MetaValidationError(
            "source 非法（可选 " + "/".join(PHOTO_SOURCES) + "）"
        )

    return PhotoMeta(
        taken_at=taken_at,
        gps_lat=gps_lat,
        gps_lng=gps_lng,
        source=source,
        raw=meta_obj,
    )
