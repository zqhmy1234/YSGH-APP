"""EXIF 拍摄时间解析（D-03：multipart 与分片上传两条注册路径共用的权威真值源）

客户端 MediaStore DATE_TAKEN 可能被扫描污染为入库时间（2026-08-24 真机实测），
照片拍摄时间以服务端解析原图字节里的 EXIF DateTimeOriginal 为准。

布局兼容（2026-08-28 Wave3 实证）：多数相机（含华为）把 36867/36868 放在
EXIF 子 IFD（0x8769），少数工具写主 IFD——两级都查，子 IFD 优先。
P0-3（审查 H3）：Image.open 前设 MAX_IMAGE_PIXELS 防解压炸弹。
"""
import io
from datetime import datetime, timedelta, timezone

MAX_IMAGE_PIXELS = 40_000_000  # 40MP 上限（超限拒绝解码）

_TAG_DATE_TIME_ORIGINAL = 36867  # EXIF 子 IFD：拍摄时间真值
_TAG_DATE_TIME = 306  # 主 IFD：修改时间（兜底）


def _parse_raw(raw: object) -> datetime | None:
    if not raw:
        return None
    try:
        # EXIF 无时区 = 相机本地时间（本设备 +08），显式按 UTC+08:00 解释，
        # 与客户端 isoString(+08:00) 一致
        return datetime.strptime(str(raw), "%Y:%m:%d %H:%M:%S").replace(
            tzinfo=timezone(timedelta(hours=8))
        )
    except ValueError:
        return None


def extract_exif_datetime(data: bytes) -> datetime | None:
    """从照片字节提取拍摄时间：子 IFD 36867 → 主 IFD 36867 → 主 IFD 306。

    非 JPEG/无 EXIF/解码失败一律静默降级 None（调用方保持客户端时间）。
    """
    try:
        from PIL import Image

        Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS
        img = Image.open(io.BytesIO(data))
        exif = img.getexif()
        try:
            dt = _parse_raw(exif.get_ifd(0x8769).get(_TAG_DATE_TIME_ORIGINAL))
            if dt is not None:
                return dt
        except Exception:  # noqa: S110 —— 无子 IFD 的畸形文件，静默走主 IFD 兜底
            pass
        dt = _parse_raw(exif.get(_TAG_DATE_TIME_ORIGINAL))
        if dt is not None:
            return dt
        return _parse_raw(exif.get(_TAG_DATE_TIME))
    except Exception:  # noqa: BLE001 —— 非 JPEG/损坏字节静默降级
        return None
