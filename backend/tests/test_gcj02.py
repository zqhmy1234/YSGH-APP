"""WGS84 → GCJ-02 转换测试（2026-10-14 · 台账 §4.20 / 审计 §2）。

## 钉住什么

坐标来源全是 **WGS84**（客户端 `uni.getLocation({type:'wgs84'})`、照片 EXIF），
而高德 REST regeo 的 `location` **只认 GCJ-02**。少了转换 ⇒ 地名系统性偏移
300~700 米，而且**不报任何错**（典型的"看起来对、其实错"）。本文件钉四件事：

  1. 偏移量落在国内应有的量级（过小 = 没转，过大 = 算错）；
  2. 与**独立参考值**对齐（北京天安门附近的公开转换值，容差 150m）；
  3. 境外坐标**原样返回**（GCJ-02 只在中国境内生效，对境外硬转会引入误差）；
  4. 纯函数（同输入同输出）。
"""
from __future__ import annotations

import math

from app.services.external.gcj02 import wgs84_to_gcj02


def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """球面距离（米）——仅本文件使用，够精即可。"""
    radius = 6371000.0
    lat1, lng1 = math.radians(a[0]), math.radians(a[1])
    lat2, lng2 = math.radians(b[0]), math.radians(b[1])
    d_lat, d_lng = lat2 - lat1, lng2 - lng1
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


def test_offset_magnitude_is_domestic_scale():
    """上海外滩：偏移量应在数百米量级（实测约 481m）。"""
    src = (31.2304, 121.4737)
    dst = wgs84_to_gcj02(*src)
    assert dst != src
    assert 300 < _distance_m(src, dst) < 800


def test_matches_independent_reference_value():
    """北京（39.908722,116.397499）的公开转换值约 (39.910035,116.403725)。

    容差取 0.0015°（≈150m）：不同实现之间的小差异不判为错，但"算错 / 没转"必红。
    """
    dst = wgs84_to_gcj02(39.908722, 116.397499)
    assert abs(dst[0] - 39.910035) < 0.0015
    assert abs(dst[1] - 116.403725) < 0.0015


def test_outside_china_returns_input_unchanged():
    """境外原样返回（东京 / 纽约 / 悉尼）——国境判定内不做偏移。"""
    for point in ((35.6762, 139.6503), (40.7128, -74.0060), (-33.8688, 151.2093)):
        assert wgs84_to_gcj02(*point) == point


def test_is_pure_and_stable():
    """纯函数：同一输入两次调用结果一致。"""
    assert wgs84_to_gcj02(30.5, 114.3) == wgs84_to_gcj02(30.5, 114.3)
