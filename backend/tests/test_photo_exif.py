"""EXIF 解析测试（2026-10-14 · 台账 §4.20）。

## 钉住什么

照片定位链路的**后半段**：从字节里取出拍摄时间与 GPS。两条都不能编造值——
拿不到就返回 None（同 `client/utils/location.uts` 的"拿不到就留空"口径）。

  · DMS（度 / 分 / 秒）+ 半球标记 → 十进制度，南 / 西取负；
  · 元素数不对 / 数值非法 / 缺字段 ⇒ None（**不补 0、不猜**）；
  · 真 JPEG 的 EXIF GPS 端到端可读（上海外滩坐标）。
"""
from __future__ import annotations

import io

import pytest
from app.services.photo_exif import (
    extract_exif_datetime,
    extract_exif_gps,
    gps_dms_to_degrees,
)

# --- DMS → 十进制度（纯函数）---

def test_dms_north_east_positive():
    """31°13'49\"N → 31.2303；121°28'25\"E → 121.4736（上海外滩）。"""
    assert round(gps_dms_to_degrees((31, 13, 49), "N"), 4) == 31.2303
    assert round(gps_dms_to_degrees((121, 28, 25), "E"), 4) == 121.4736


def test_dms_south_west_negative():
    """S / W 半球取负——否则南半球 / 西半球的照片会落到镜像位置。"""
    assert gps_dms_to_degrees((33, 52, 0), "S") < 0
    assert gps_dms_to_degrees((74, 0, 0), "W") < 0


def test_dms_missing_hemisphere_treated_as_positive():
    """半球标记缺失时取正值（EXIF 里 `GPSLatitudeRef` 一般都在；缺失时不做符号猜测）。"""
    assert gps_dms_to_degrees((31, 13, 49), None) > 0
    assert gps_dms_to_degrees((31, 13, 49), "") > 0


@pytest.mark.parametrize("dms", [None, (), (31, 13), ("a", "b", "c"), (31, 13, None)])
def test_dms_invalid_returns_none(dms):
    """结构不对 / 非数值 ⇒ None（**不补 0**：编造坐标比没有坐标更糟）。"""
    assert gps_dms_to_degrees(dms, "N") is None


# --- 端到端：真 JPEG ---

def _jpeg_with_gps(
    lat_parts=(31, 13, 49),
    lng_parts=(121, 28, 25),
    lat_ref: str = "N",
    lng_ref: str = "E",
) -> bytes:
    """构造一张带 EXIF GPS 的最小 JPEG（不引入外部样例文件）。

    ⚠️ 写入必须用 `IFDRational`：EXIF 的 DMS 是 RATIONAL 数组，直接塞嵌套 int 元组
    会在 Pillow 序列化时炸（`abs(tuple)` TypeError）——这是**构造侧**的 API 要求，
    与解析侧无关。
    """
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)  # GPSInfo IFD：Pillow 会把该 dict 挂进 _ifds，可直接改
    gps[1] = lat_ref
    gps[2] = tuple(IFDRational(value, 1) for value in lat_parts)
    gps[3] = lng_ref
    gps[4] = tuple(IFDRational(value, 1) for value in lng_parts)
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (128, 128, 128)).save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _jpeg_with_datetime(*, tag: int, value: str) -> bytes:
    """构造带指定 EXIF 时间标签的最小 JPEG。"""
    from PIL import Image

    exif = Image.Exif()
    exif[tag] = value
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (128, 128, 128)).save(
        buf, format="JPEG", exif=exif.tobytes()
    )
    return buf.getvalue()


def test_extract_exif_gps_from_real_jpeg():
    """真 JPEG 端到端：写入 → 保存 → 解析。

    构造侧若失败（Pillow 写 GPS IFD 的 API 随版本变动）则 skip 且**明确标注是
    "构造侧限制"**——既不伪装成解析缺陷，也不因此掩盖解析回归。
    """
    try:
        data = _jpeg_with_gps()
    except Exception as exc:  # noqa: BLE001 —— 构造侧能力问题
        pytest.skip(f"本机 Pillow 无法写出 GPS IFD（构造侧限制）：{exc}")
    got = extract_exif_gps(data)
    assert got is not None, "解析侧必须能读出刚写进去的 GPS"
    lat, lng = got
    assert round(lat, 3) == 31.230
    assert round(lng, 3) == 121.474


def test_extract_exif_gps_rejects_zero_pair():
    """(0,0) 是常见空占位，必须返回 None，不能落到几内亚湾。"""
    try:
        data = _jpeg_with_gps(lat_parts=(0, 0, 0), lng_parts=(0, 0, 0))
    except Exception as exc:  # noqa: BLE001 —— 构造侧能力问题
        pytest.skip(f"本机 Pillow 无法写出 GPS IFD（构造侧限制）：{exc}")
    assert extract_exif_gps(data) is None


def test_extract_exif_datetime_falls_back_to_datetime_tag():
    """真实相机常只写 tag 306；必须兼容，否则合法拍摄时间会被当成 None。"""
    data = _jpeg_with_datetime(tag=306, value="2025:06:12 16:21:31")
    got = extract_exif_datetime(data)
    assert got is not None
    assert got.isoformat() == "2025-06-12T16:21:31+08:00"


def test_extract_exif_datetime_prefers_datetime_original():
    """两个标签都存在时，DateTimeOriginal 是更权威的相机拍摄时间。"""
    from PIL import Image

    exif = Image.Exif()
    exif[306] = "2026:01:01 00:00:00"
    exif[36867] = "2025:06:12 16:21:31"
    buf = io.BytesIO()
    Image.new("RGB", (4, 4), (128, 128, 128)).save(
        buf, format="JPEG", exif=exif.tobytes()
    )

    got = extract_exif_datetime(buf.getvalue())
    assert got is not None
    assert got.isoformat() == "2025-06-12T16:21:31+08:00"


def test_extract_returns_none_for_non_jpeg():
    """非 JPEG 字节 / 空字节 ⇒ None（静默降级，不得抛异常拖垮上传）。"""
    assert extract_exif_gps(b"not an image at all") is None
    assert extract_exif_datetime(b"not an image at all") is None
    assert extract_exif_gps(b"") is None
    assert extract_exif_datetime(b"") is None
