"""AGG-016 参考端回归（S-AG-2 · Python 期望值语义锁）

夹具期望由 Python 同参计算（scripts/gen_agg_fixtures.py）；本测试锁住
关键语义，防止参考端（含 tz_offset_minutes 参数）漂移导致端侧双跑误报：
  - 连拍折叠：<5s 照片被折叠（保留首张），不参与簇
  - 深夜归属：23:30-1:00 → 前一天（含 tz 偏移生效）
  - GPS 漂移：超速坐标置空，仍按时间归组
  - 无 GPS 按时间窗归组；散片并入日卡片（is_sparse）
"""
from datetime import datetime, timedelta, timezone

from app.services.event_aggregation.pipeline import RawPhoto, preprocess
from app.services.event_aggregation.st_dbscan import l1_daily_aggregate, st_dbscan

TZ = 480  # 上海（与夹具生成同参）


def _p(pid: str, ms: int, lat=None, lng=None, tags=None) -> RawPhoto:
    return RawPhoto(
        id=pid,
        ts=datetime.fromtimestamp(ms / 1000, tz=timezone.utc),
        lat=lat, lng=lng, tags=tags or [],
    )


def _local_ms(y: int, m: int, d: int, hh: int, mm: int) -> int:
    """本地墙钟（UTC+8）→ epoch ms"""
    dt = datetime(y, m, d, hh, mm, tzinfo=timezone.utc) - timedelta(minutes=TZ)
    return int(dt.timestamp() * 1000)


def _run(photos):
    pts = preprocess(photos)
    clusters = st_dbscan(pts, eps_t_sec=3600.0, eps_s_m=500.0, min_pts=3)
    clustered = {p.id for cl in clusters for p in cl}
    noise = [p for p in pts if p.id not in clustered]
    days = l1_daily_aggregate(clusters, noise, tz_offset_minutes=TZ)
    return clusters, days


def test_burst_fold_keeps_first_photo():
    """连拍折叠：<5s 只保留首张（p2/p3 折叠掉）→ 剩 2 张不足 min_pts，散片入日卡片"""
    t0 = _local_ms(2026, 8, 24, 10, 0)
    photos = [
        _p("p1", t0, 31.2304, 121.4737),
        _p("p2", t0 + 2000, 31.2304, 121.4737),
        _p("p3", t0 + 4000, 31.2304, 121.4737),
        _p("p4", t0 + 10000, 31.2304, 121.4737),
    ]
    clusters, days = _run(photos)
    flat = [p.id for cl in clusters for p in cl]
    assert flat == [], "折叠后仅 2 张，不足 min_pts=3，不应成簇"
    assert sorted(p.id for p in days[0]["photos"]) == ["p1", "p4"], "p2/p3 被折叠掉"
    assert len(days) == 1 and days[0]["is_sparse"] is True


def test_night_boundary_attributed_to_prev_day_with_tz():
    """深夜归属（墙钟规则，与 tz 无关）：23:40→前一天；00:20/00:30→前一天；01:01→当天"""
    photos = [
        _p("p1", _local_ms(2026, 8, 24, 23, 40), 31.2304, 121.4737),
        _p("p2", _local_ms(2026, 8, 24, 23, 50), 31.2304, 121.4737),
        _p("p3", _local_ms(2026, 8, 25, 0, 20), 31.2304, 121.4737),
        _p("p4", _local_ms(2026, 8, 25, 0, 30), 31.2304, 121.4737),
        _p("p5", _local_ms(2026, 8, 25, 1, 1), 31.2304, 121.4737),
    ]
    _, days = _run(photos)
    by_date = {d["date"]: sorted(p.id for p in d["photos"]) for d in days}
    assert by_date["2026-08-23"] == ["p1", "p2"], "8/24 23:40 深夜拍摄应归属 8/23"
    assert by_date["2026-08-24"] == ["p3", "p4"], "8/25 00:xx 应归属 8/24"
    assert by_date["2026-08-25"] == ["p5"], "01:01 已出深夜窗，归属当天"


def test_utc_bucket_no_shift():
    """tz=0（UTC 日界）：深夜规则按墙钟生效——23:40 UTC 归属前一天 2026-08-23"""
    t0 = datetime(2026, 8, 24, 23, 40, tzinfo=timezone.utc)
    photos = [_p("p1", int(t0.timestamp() * 1000), 31.2304, 121.4737)]
    pts = preprocess(photos)
    days = l1_daily_aggregate([], pts, tz_offset_minutes=0)
    assert days[0]["date"] == "2026-08-23", "23:40 墙钟触发深夜规则，归属前一天"


def test_gps_drift_nulled_still_clusters():
    """GPS 漂移：10s 内 100km → 坐标置空；仍按时间窗与同组照片成簇"""
    t0 = _local_ms(2026, 8, 24, 16, 0)
    photos = [
        _p("p1", t0, 31.2304, 121.4737),
        _p("p2", t0 + 10000, 31.90, 122.10),
        _p("p3", t0 + 20000, 31.2306, 121.4740),
    ]
    clusters, _ = _run(photos)
    flat = [p.id for cl in clusters for p in cl]
    assert sorted(flat) == ["p1", "p2", "p3"], "漂移照片置空 GPS 后应仍按时间成簇"
    # 漂移照片坐标确实被置空
    pts = preprocess(photos)
    by_id = {p.id: p for p in pts}
    assert by_id["p2"].lat is None and by_id["p2"].lng is None
