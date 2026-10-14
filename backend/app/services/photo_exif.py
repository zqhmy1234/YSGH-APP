"""照片 EXIF 解析（拍摄时间 + GPS）（2026-10-14 · 从 `api/contents/__init__.py` 下沉）。

## 为什么单独成模块

两个 EXIF 助手原本内联在 `api/contents/__init__.py` 里。2026-10-14 新增 EXIF GPS
兜底后该文件 **642 行 > 结构棘轮阈值 600**，被 `review_agent` 结构轴拦下。
按门禁给的处置建议"拆分或显式申请基线"，这里选择**拆分**——而且不只是为了过门禁：

  · 它们与 HTTP 协议无关（纯 `bytes → 值`），放在 services 层更合归属；
  · 依赖 `PIL` 这种重库，下沉后 API 层不再直接持有图像处理细节；
  · 分片上传路径（`api/upload.py`）若将来要解析 EXIF，可直接复用，
    不必反向 import API 层。

## 口径（与项目「失败不编造」铁律一致）

  · 非 JPEG / 无 EXIF / 解析异常 ⇒ **一律返回 None**，由调用方落缺省；**绝不编造值**。
  · EXIF 时间为「无时区的相机本地时间」：本设备按 **UTC+08:00** 解释，
    与客户端 `isoString(+08:00)` 一致（客户端 DATE_TAKEN 可能被 MediaProvider
    写成扫描时间——2026-08-24 真机实测，故以后端 EXIF 为准）。
  · EXIF GPS 为 **WGS84**（国际标准，与客户端 `type: 'wgs84'` 同源）；
    转 GCJ-02 交高德的动作统一在 `amap.get_place` 入口做（单一转换点）。
  · `Image.open` 前设 `MAX_IMAGE_PIXELS` 防解压炸弹（P0-3 · 审查 H3：`getexif` 本身
    不解码像素，但 open 阶段的尺寸检查会触发 `DecompressionBombError`）。
"""
from __future__ import annotations

import io
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("yishu.photo_exif")

# PIL 解压炸弹防护（P0-3 · 审查 H3）：40MP 上限（超限拒绝解码）
MAX_IMAGE_PIXELS = 40_000_000


def gps_dms_to_degrees(dms: Any, ref: Any) -> float | None:
    """EXIF GPS 的「(度, 分, 秒) 三元组 + 半球标记」→ 十进制度。

    返回 None 表示该值不可用（缺字段 / 元素数不对 / 数值非法）——**不猜、不补 0**：
    编造坐标比没有坐标更糟（同 `client/utils/location.uts` 的口径）。
    """
    if not dms or not isinstance(dms, (tuple, list)) or len(dms) != 3:
        return None
    try:
        degrees = float(dms[0])
        minutes = float(dms[1])
        seconds = float(dms[2])
    except (TypeError, ValueError):
        return None
    value = degrees + minutes / 60.0 + seconds / 3600.0
    if str(ref or "").strip().upper() in ("S", "W"):
        value = -value
    return value


def extract_exif_gps(data: bytes) -> tuple[float, float] | None:
    """从照片字节提取 EXIF GPS（**原始 WGS84**）；无 EXIF / 无 GPS 返回 None。

    ## 为什么要有它（2026-10-14 · 台账 §4.20 / 审计 §2）

    手动拍照/选图这条路径**此前从不带地点**：客户端把经纬度硬编码为 `null`
    （`RecordSheet.savePhoto`），而后端也不看照片自身的 EXIF GPS ⇒ 用户拍的每张
    照片 `gps_lat/gps_lng` 恒空、`place` 恒空，而文字/语音却都有地点。

    客户端已补（带上打开记录面板时取到的定位）；本函数是**独立兜底**：即便客户端
    meta 缺失（老版本客户端 / 后台相册导入路径），照片自身的坐标仍能被采纳。

    ## 为什么用 `get_ifd(34853)` 而不是手写 Rational 遍历

    GPS 信息在 EXIF IFD `34853`；`get_ifd` 直接给出
    `{1: 'N', 2: ((d, m, s), (1, 1, 1)), 3: 'E', 4: ...}` —— PIL 已处理字节序与
    Rational 归一化，比自己遍历 tag 更短也更稳（与 `extract_exif_datetime` 同源写法）。
    """
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        img = Image.open(io.BytesIO(data))
        gps = img.getexif().get_ifd(34853)
        if not gps:
            return None
        lat = gps_dms_to_degrees(gps.get(2), gps.get(1))
        lng = gps_dms_to_degrees(gps.get(4), gps.get(3))
        if lat is None or lng is None:
            return None
        if not (-90 <= lat <= 90) or not (-180 <= lng <= 180):
            return None
        return lat, lng
    except Exception:  # noqa: BLE001 —— 非 JPEG / 无 EXIF 静默降级（同 extract_exif_datetime）
        return None


def extract_exif_datetime(data: bytes) -> datetime | None:
    """从照片字节提取 EXIF DateTimeOriginal（相机拍摄时间真值）。

    客户端 DATE_TAKEN 可能被 MediaProvider 写成扫描时间（2026-08-24 真机实测），
    故以后端 EXIF 解析为准：EXIF 无时区 = 相机本地时间（本设备 +08），显式按
    UTC+08:00 解释，与客户端 isoString(+08:00) 一致。
    """
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        raw = exif.get(36867)  # DateTimeOriginal
        if raw:
            return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(
                tzinfo=timezone(timedelta(hours=8))
            )
    except Exception:  # noqa: BLE001 —— 非 JPEG/无 EXIF 静默降级
        return None
    return None
