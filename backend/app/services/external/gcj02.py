"""WGS84 → GCJ-02（火星坐标）转换（2026-10-14 · 台账 §4.20 / 审计 §2）。

## 为什么需要它（一个**系统性偏移**根因）

本项目坐标的来源全是 **WGS84**：

  · 客户端 `uni.getLocation({type: 'wgs84'})`（`client/utils/location.uts:72`）；
  · 照片 EXIF 的 GPS（国际标准就是 WGS84）；
  · 相册 MediaStore 的 `LATITUDE`（同样取自 EXIF）。

而高德 REST 逆地理（`https://restapi.amap.com/v3/geocode/regeo`）的 `location` 参数
**只认 GCJ-02**（国测局加密后的国内标准；REST 接口没有 `coordsys` 参数可切换）。
两者在中国境内相差约 **300~700 米** ⇒ 不转换的话，"拍照地点"会系统性偏到隔壁街区
甚至跨 POI，而且**不会报任何错**——典型的"看起来对、其实错"，正是本项目最贵的一类。

## 口径（单一转换点）

**所有对外发出的地理编码请求前**（`amap.get_place` 入口）转换一次；
`contents.gps_lat/gps_lng` 仍按**原始 WGS84** 入库（诚实、可换其他地图服务）。
境外（`_out_of_china`）GCJ-02 与 WGS84 事实上一致，原样返回。

## 算法与精度

标准公开算法（克拉索夫斯基椭球 + 二次/三角拟合），误差 <1m，
对 geohash 精度 6（≈1.2km 格子）的场景绰绰有余。纯函数、无依赖、可单测。
"""
from __future__ import annotations

import math

# 克拉索夫斯基椭球（GCJ-02 算法固定的椭球参数）
_A = 6378245.0
# 偏心率平方
_EE = 0.00669342162296594323


def _out_of_china(lat: float, lng: float) -> bool:
    """粗略国境判定：境外不做偏移（GCJ-02 只在中国境内生效）。"""
    return not (73.66 < lng < 135.05 and 3.86 < lat < 53.55)


def _transform_lat(x: float, y: float) -> float:
    ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(y * math.pi) + 40.0 * math.sin(y / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(y / 12.0 * math.pi) + 320.0 * math.sin(y * math.pi / 30.0)) * 2.0 / 3.0
    return ret


def _transform_lng(x: float, y: float) -> float:
    ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
    ret += (20.0 * math.sin(6.0 * x * math.pi) + 20.0 * math.sin(2.0 * x * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(x * math.pi) + 40.0 * math.sin(x / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(x / 12.0 * math.pi) + 300.0 * math.sin(x / 30.0 * math.pi)) * 2.0 / 3.0
    return ret


def wgs84_to_gcj02(lat: float, lng: float) -> tuple[float, float]:
    """WGS84 → GCJ-02；境外坐标原样返回（纯函数）。"""
    if _out_of_china(lat, lng):
        return lat, lng
    d_lat = _transform_lat(lng - 105.0, lat - 35.0)
    d_lng = _transform_lng(lng - 105.0, lat - 35.0)
    rad_lat = lat / 180.0 * math.pi
    magic = math.sin(rad_lat)
    magic = 1 - _EE * magic * magic
    sqrt_magic = math.sqrt(magic)
    d_lat = (d_lat * 180.0) / ((_A * (1 - _EE)) / (magic * sqrt_magic) * math.pi)
    d_lng = (d_lng * 180.0) / (_A / sqrt_magic * math.cos(rad_lat) * math.pi)
    return lat + d_lat, lng + d_lng
