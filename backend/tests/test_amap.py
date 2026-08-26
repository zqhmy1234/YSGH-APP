"""高德逆地理编码测试（外部API清单 #5 · 2026-08-25 实现）

覆盖：
  - geohash 编码正确性（参考值：北京 39.9042,116.4074 → wx4g0t）
  - mock 模式 regeo 抛错（调用方降级契约）
  - get_place：开发 mock 回退（确定性、不写缓存）
  - get_place：生产拒绝 mock 回退（返回 None）
  - get_place：缓存命中（30 天内不调 API）
  - get_place：缓存过期（>30 天重取并刷新，高德合规）
  - get_place：None 坐标直接返回 None
前置：PG yishu 库（geo_cache 表，alembic 迁移后存在）
"""
from datetime import datetime, timedelta, timezone

import pytest
from app.core.config import settings
from app.db.models import GeoCache
from app.db.session import SessionLocal
from app.services.external import amap as amap_svc
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select

pytestmark = pytest.mark.integration

# 本文件全部测试写入的缓存 key（geo_cache 主键 = geohash）：teardown 按这些 key
# 定向删除，不再清空整表（R8#13——防连带清掉未来其他模块/生产联调写入的缓存）。
_TEST_COORDS = [(31.2304, 121.4737)]


def _test_geohashes() -> list[str]:
    return [amap_svc.encode_geohash(lat, lng) for lat, lng in _TEST_COORDS]


@pytest.fixture(autouse=True)
def _ensure_mock_mode():
    """测试必须跑在 mock 模式（不产生费用、不依赖网络）"""
    assert settings.mock_external_ai is True, "测试环境要求 MOCK_EXTERNAL_AI=true"
    yield


@pytest.fixture()
def db():
    session = SessionLocal()
    yield session
    # R8#13：整表清空 → 只删本测试写入的 geohash key（其余 geo_cache 行不动）
    session.execute(sa_delete(GeoCache).where(GeoCache.geohash.in_(_test_geohashes())))
    session.commit()
    session.close()


# --- geohash 纯函数 ---

def test_geohash_beijing_reference():
    """参考值（与 geohash2 独立库交叉验证）：北京天安门 geohash 精度 6 = wx4g0b"""
    assert amap_svc.encode_geohash(39.9042, 116.4074, 6) == "wx4g0b"


def test_geohash_deterministic_and_distinct():
    """同点同值；不同地点不同值"""
    assert amap_svc.encode_geohash(31.2304, 121.4737) == amap_svc.encode_geohash(31.2304, 121.4737)
    assert amap_svc.encode_geohash(31.2304, 121.4737) != amap_svc.encode_geohash(39.9042, 116.4074)


def test_geohash_precision_length():
    assert len(amap_svc.encode_geohash(31.23, 121.47, 6)) == 6
    assert len(amap_svc.encode_geohash(31.23, 121.47, 5)) == 5


# --- regeo 契约 ---

def test_regeo_raises_in_mock():
    """mock 模式无真实 key → 抛错，调用方走降级（同 RAG 外部服务契约）"""
    with pytest.raises(RuntimeError):
        amap_svc.regeo(31.2304, 121.4737)


# --- get_place 服务 ---

def test_get_place_dev_mock_fallback(db, monkeypatch):
    """开发/测试：regeo 不可用 → 确定性 mock 地名，且不写缓存"""
    def boom(lat, lng):
        raise RuntimeError("高德不可用")

    monkeypatch.setattr(amap_svc, "regeo", boom)
    place = amap_svc.get_place(db, 31.2304, 121.4737)
    assert place and place.startswith("示例区·")
    # mock 不落缓存（R8#13：按本测试 geohash 定向计数，不清整表）
    assert db.execute(
        select(func.count()).select_from(GeoCache).where(
            GeoCache.geohash.in_(_test_geohashes())
        )
    ).scalar() == 0


def test_get_place_production_rejects_mock(db, monkeypatch):
    """生产：regeo 不可用 → 返回 None（拒绝 mock 假地名落库）"""
    def boom(lat, lng):
        raise RuntimeError("高德不可用")

    monkeypatch.setattr(amap_svc, "regeo", boom)
    monkeypatch.setattr(settings, "app_env", "production")
    assert amap_svc.get_place(db, 31.2304, 121.4737) is None
    # mock 不落缓存（R8#13：按本测试 geohash 定向计数，不清整表）
    assert db.execute(
        select(func.count()).select_from(GeoCache).where(
            GeoCache.geohash.in_(_test_geohashes())
        )
    ).scalar() == 0


def test_get_place_cache_hit_no_api(db, monkeypatch):
    """缓存命中（30 天内）→ 直接返回，不调 API"""
    def boom(lat, lng):
        raise AssertionError("缓存命中不应调 API")

    monkeypatch.setattr(amap_svc, "regeo", boom)
    db.add(GeoCache(geohash=amap_svc.encode_geohash(31.2304, 121.4737), place="上海外滩"))
    db.commit()

    assert amap_svc.get_place(db, 31.2304, 121.4737) == "上海外滩"


def test_get_place_cache_expired_refetch(db, monkeypatch):
    """缓存过期（>30 天，高德合规）→ 重取并刷新缓存"""
    calls = []

    def fake_regeo(lat, lng):
        calls.append((lat, lng))
        return {"province": "上海市", "city": "上海市", "place": "陆家嘴"}

    monkeypatch.setattr(amap_svc, "regeo", fake_regeo)
    stale = GeoCache(
        geohash=amap_svc.encode_geohash(31.2304, 121.4737),
        place="旧地点",
        updated_at=datetime.now(timezone.utc) - timedelta(days=31),
    )
    db.add(stale)
    db.commit()

    place = amap_svc.get_place(db, 31.2304, 121.4737)
    assert place == "陆家嘴"
    assert len(calls) == 1
    db.expire_all()
    # 只按本测试 geohash 定向读回（R8#13：不再 DELETE 整表）
    row = db.execute(
        sa_delete(GeoCache)
        .where(GeoCache.geohash.in_(_test_geohashes()))
        .returning(GeoCache.place)
    ).one()
    assert row[0] == "陆家嘴"  # 缓存已刷新


def test_get_place_none_coords(db, monkeypatch):
    """无 GPS 坐标 → 直接 None，不碰缓存/API"""
    def boom(lat, lng):
        raise AssertionError("不应调用")

    monkeypatch.setattr(amap_svc, "regeo", boom)
    assert amap_svc.get_place(db, None, None) is None
