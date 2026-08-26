"""高德逆地理编码（GPS→地名 · 外部API清单 #5 / MVP v3 逆编码=高德，已拍板）

调用方：照片 AI 管线（photo GPS → contents.place，事件聚合/搜索地点过滤的元数据源）。
缓存：geo_cache 表（geohash 精度 6 ≈1.2km 格子键，一次聚餐 30 张 = 1 次调用）。
合规：逆地理编码结果不可缓存超 30 天（读取时校验 updated_at 年龄，过期重取）；
      客户端展示需标注"高德地图"版权（字号≥12px，产品 UI 侧落实）。
成本：个人认证 5000 次/日免费（100 用户 × 5 张/天 完全覆盖，内测 0 元）。
Mock：MOCK_EXTERNAL_AI=true 或未配 key → regeo 抛 RuntimeError（调用方降级）；
      get_place 在开发/测试模式回退确定性 mock（不落缓存），生产环境拒绝 mock 落库
      （同 ASR 转写审查 CRITICAL 修复：防假地名污染真实记忆库）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import GeoCache
from app.services.external.retry import with_retry

logger = logging.getLogger("yishu.amap")

AMAP_ENDPOINT = "https://restapi.amap.com/v3/geocode/regeo"
# 高德合规：逆地理结果缓存上限 30 天
GEO_CACHE_MAX_DAYS = 30

_BASE32 = "0123456789bcdefghjkmnpqrstuvwxyz"


def encode_geohash(lat: float, lng: float, precision: int = 6) -> str:
    """geohash 编码（精度 6 ≈ 1.2km×0.6km 格子）——纯函数，单测覆盖

    与主流实现一致（参考值：北京 39.9042,116.4074 → 'wx4g0t'）。
    """
    lat_range, lng_range = (-90.0, 90.0), (-180.0, 180.0)
    bit = 0
    ch = 0
    out: list[str] = []
    even = True
    while len(out) < precision:
        if even:
            mid = (lng_range[0] + lng_range[1]) / 2
            if lng >= mid:
                ch |= 1 << (4 - bit)
                lng_range = (mid, lng_range[1])
            else:
                lng_range = (lng_range[0], mid)
        else:
            mid = (lat_range[0] + lat_range[1]) / 2
            if lat >= mid:
                ch |= 1 << (4 - bit)
                lat_range = (mid, lat_range[1])
            else:
                lat_range = (lat_range[0], mid)
        even = not even
        if bit < 4:
            bit += 1
        else:
            out.append(_BASE32[ch])
            bit = 0
            ch = 0
    return "".join(out)


def _amap_available() -> bool:
    """高德可用判定：非 mock 且已配 key"""
    return not settings.mock_external_ai and bool(settings.amap_api_key)


@with_retry(retries=3, backoff=(1, 2, 4), timeout=15)
def regeo(lat: float, lng: float) -> dict:
    """高德逆地理（真实调用）：location 参数序为 经度,纬度

    返回 {province, city, place, formatted_address}；失败抛异常由调用方静默降级。
    """
    if not _amap_available():
        raise RuntimeError("高德未配置（MOCK 或缺 key），逆地理不可用")
    import httpx

    resp = httpx.get(
        AMAP_ENDPOINT,
        params={
            "key": settings.amap_api_key,
            "location": f"{lng},{lat}",
            "extensions": "base",
            "output": "json",
        },
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    if str(data.get("status")) != "1":
        raise RuntimeError(f"高德 regeo 失败: {data.get('info')}")
    regeo_data = data.get("regeocode") or {}
    comp = regeo_data.get("addressComponent") or {}
    return {
        "province": comp.get("province") or "",
        "city": comp.get("city") or comp.get("province") or "",
        "place": regeo_data.get("formatted_address") or "",
        "formatted_address": regeo_data.get("formatted_address") or "",
    }


def _mock_regeo(lat: float, lng: float) -> dict:
    """确定性 mock（开发/测试联调零费用）：geohash 前缀生成稳定假地名

    带 mock=True 标记：get_place 据此跳过缓存写入（假地名不污染缓存）。
    """
    gh = encode_geohash(lat, lng)
    return {
        "mock": True,
        "province": "示例省",
        "city": "示例市",
        "place": f"示例区·{gh}",
        "formatted_address": f"示例区·{gh}",
    }


def get_place(db, lat: float, lng: float) -> str | None:
    """照片管线入口：GPS → 地名（缓存优先，≤30 天；失败静默）

    返回 place 或 None；生产环境 mock 结果拒绝落库（防假地名污染）。
    """
    if lat is None or lng is None:
        return None
    gh = encode_geohash(lat, lng)

    # 1. 缓存命中（30 天内）直接复用
    try:
        row = db.execute(
            select(GeoCache).where(GeoCache.geohash == gh)
        ).scalar_one_or_none()
    except Exception as exc:  # noqa: BLE001 —— 缓存读失败不阻断
        logger.warning("geo_cache 读取失败 %s: %s", gh, exc)
        row = None
    if row is not None and row.updated_at is not None:
        age = datetime.now(timezone.utc) - row.updated_at
        if age <= timedelta(days=GEO_CACHE_MAX_DAYS) and row.place:
            return row.place

    # 2. 缓存缺失/过期 → 真实调用（mock 模式抛错走降级）
    try:
        info = regeo(lat, lng)
    except Exception as exc:  # noqa: BLE001
        if settings.app_env == "production":
            logger.warning("逆地理失败（生产，不回退 mock）%s,%s: %s", lat, lng, exc)
            return None
        logger.info("逆地理 mock 回退 %s,%s: %s", lat, lng, exc)
        info = _mock_regeo(lat, lng)

    if not info.get("place"):
        return None
    # mock 结果（开发/测试回退）：不写缓存；生产已在上面拒绝 mock 回退
    if info.get("mock"):
        return info["place"]

    # 真实结果 → 写缓存（R2#2 事务边界：缓存必须**中途落库**，且不得在调用方
    # 管线事务内嵌套 commit——显式用独立 Session 写（独立事务），外层事务回滚
    # 不影响缓存；缓存写冲突只回滚本次写入，不影响调用方事务）
    from app.db.session import SessionLocal

    try:
        cache_db = SessionLocal()
        try:
            with cache_db.begin_nested():  # SAVEPOINT：本次缓存写失败只回滚本写入
                cache_db.merge(GeoCache(
                    geohash=gh,
                    place=info["place"],
                    city=info.get("city") or "",
                    province=info.get("province") or "",
                ))
            cache_db.commit()
        except Exception as exc:  # noqa: BLE001 —— 缓存写失败不影响返回
            cache_db.rollback()
            logger.warning("geo_cache 写入失败 %s: %s", gh, exc)
        finally:
            cache_db.close()
    except Exception as exc:  # noqa: BLE001 —— 独立会话异常同样不影响返回
        logger.warning("geo_cache 会话异常 %s: %s", gh, exc)
    return info["place"]
