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


# --- Wave2-AgentD：GPS 众数纠正 / L2 地点域连续 / L3 7 天窗 / 封面 / 增量先匹配 ---

def _pc(pid: str, ts: datetime, lat=None, lng=None, tags=None, **kw) -> RawPhoto:
    """RawPhoto 构造（透传 ocr/quality/face）"""
    return RawPhoto(id=pid, ts=ts, lat=lat, lng=lng, tags=tags or [], **kw)


def test_gps_single_point_mode_correction_pulls_back():
    """B3-3 单点漂移众数纠正：6 张众数 + 1 张 100km 外（超物理上限）→ 拉回众数格中心"""
    t0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    photos = [_pc(f"m{i}", t0 + timedelta(minutes=i * 2), 31.2304, 121.4737) for i in range(6)]
    photos.append(_pc("drift", t0 + timedelta(seconds=10), 31.90, 122.10))
    pts = preprocess(photos)
    d = {p.id: p for p in pts}
    assert d["drift"].gps_state == "corrected"
    assert d["drift"].lat == 31.23 and d["drift"].lng == 121.474, "众数拉回（网格中心）"
    clusters, _ = _run(photos)
    flat = [p.id for cl in clusters for p in cl]
    assert "drift" in flat, "拉回后应并入众数事件簇（不产生新簇）"


def test_l2_place_continuity_trigger():
    """B3-2 L2 地点域连续触发：跨天移动 ≤5km/12hr → 候选；>5km 断裂 → 不候选"""
    from app.services.event_aggregation.pipeline import l2_candidates

    t0 = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    # 连续组：3 天每天 4 张，每天移动 3km（标签各不相同 → 无主导标签，靠地点域连续）
    near = []
    for day in range(3):
        near.append([
            _pc(f"n{day}-{i}", t0 + timedelta(days=day, minutes=i * 10),
                lat=31.23 + day * 0.027, lng=121.47, tags=[f"tag{day}"])
            for i in range(4)
        ])
    # 断裂组：3 次每天移动 20km，且跨度 <12h（地点域连续被打破）
    far = []
    for day in range(3):
        far.append([
            _pc(f"f{day}-{i}", t0 + timedelta(hours=day * 8, minutes=i * 10),
                lat=31.23 + day * 0.18, lng=121.47, tags=[f"ftag{day}"])  # 每天 ~20km，<12h
            for i in range(4)
        ])
    near_cands = l2_candidates(near)
    far_cands = l2_candidates(far)
    assert any(c["tag"] is None and len(c["cluster"]) == 12 for c in near_cands), \
        "地点域连续（≤5km/12hr）应触发 L2 候选"
    assert not any(len(c["cluster"]) == 12 for c in far_cands), "地点断裂（>5km/12hr）不应成候选"


def test_l3_seven_day_window():
    """B3-2 L3 7 天窗：同标签 7 天内 ≥3 次（跨天）成流；超出 7 天窗不计数"""
    from app.services.event_aggregation.pipeline import l3_candidates

    t0 = datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc)
    within = [_pc(f"w{i}", t0 + timedelta(days=i), 31.23, 121.47, tags=["备考"]) for i in range(3)]
    outside = [_pc(f"o{i}", t0 + timedelta(days=i * 10), 31.23, 121.47, tags=["散落"]) for i in range(3)]
    cands = l3_candidates(within + outside)
    by_tag = {c["tag"]: c for c in cands}
    assert "备考" in by_tag and by_tag["备考"]["count"] == 3, "7 天窗内 3 次（跨天）成流"
    assert "散落" not in by_tag, "3 次跨 30 天（超出 7 天窗）不应成流"
    assert set(by_tag["备考"]["cluster"]) == {f"w{i}" for i in range(3)}, "候选携带 7 天窗成员"


def test_l3_lifecycle_states():
    """B3-2 L3 生命周期：活跃(≤30 天)→静默(30-90 天)→归档(>90 天)"""
    from datetime import timezone

    from app.services.event_aggregation.pipeline import l3_lifecycle

    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    last = datetime(2026, 1, 10, tzinfo=timezone.utc)
    assert l3_lifecycle(start, last, now=datetime(2026, 1, 20, tzinfo=timezone.utc))["state"] == "active"
    assert l3_lifecycle(start, last, now=datetime(2026, 2, 20, tzinfo=timezone.utc))["state"] == "silent"
    assert l3_lifecycle(start, last, now=datetime(2026, 5, 1, tzinfo=timezone.utc))["state"] == "archived"


def test_cover_face_priority_and_centering():
    """B3-4 封面：人脸优先 + 质量分；L2 时间居中 / L3 不居中"""
    from app.services.event_aggregation.pipeline import _pick_cover

    t0 = datetime(2026, 8, 24, 10, 0, tzinfo=timezone.utc)
    photos = [
        _pc("a", t0 + timedelta(minutes=0), 31.23, 121.47, quality=0.3),
        _pc("b", t0 + timedelta(minutes=30), 31.23, 121.47, quality=0.5),
        _pc("c", t0 + timedelta(minutes=60), 31.23, 121.47, quality=0.9),
        _pc("d", t0 + timedelta(minutes=90), 31.23, 121.47, quality=0.4, face_count=2),
    ]
    assert _pick_cover(photos, level=2) == "d", "人脸优先于质量分"
    # 无脸：L2 时间居中 + 质量分（60min 中点 → c 居中且质量高）
    no_face = [
        _pc("a", t0, 31.23, 121.47, quality=0.3),
        _pc("c", t0 + timedelta(minutes=60), 31.23, 121.47, quality=0.9),
    ]
    assert _pick_cover(no_face, level=2) == "c"
    # L3 不居中：同等条件下取最新（b 晚于 a）
    l3 = [_pc("a", t0, 31.23, 121.47, quality=0.5), _pc("b", t0 + timedelta(days=30), 31.23, 121.47, quality=0.5)]
    assert _pick_cover(l3, level=3) == "b"


def test_incremental_match_first_then_split():
    """B3-6 增量先匹配后分裂：新照片并入现有簇（时间窗+地点邻近），超限才独立成簇"""
    from app.services.event_aggregation.pipeline import aggregate, incremental_aggregate

    t0 = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)
    base = [_pc(f"b{i}", t0 + timedelta(minutes=i * 10), 31.2304, 121.4737) for i in range(3)]
    r1 = aggregate(base)
    assert len(r1.l0_clusters) == 1

    # ① 邻近新照片（+20min，同地点）→ 并入现有簇（先匹配）
    near = _pc("near", t0 + timedelta(minutes=50), 31.2305, 121.4738)
    r2 = incremental_aggregate(r1, [near])
    assert len(r2.l0_clusters) == 1, "邻近新照片应并入现有簇，不分裂"
    assert any("near" in {p.id for p in cl} for cl in r2.l0_clusters), "near 已并入旧簇"

    # ② 远处 3 张（20km，自成 min_pts）→ 超限才独立成簇
    far = [_pc(f"far{i}", t0 + timedelta(days=1, minutes=i * 10), 31.4, 121.7) for i in range(3)]
    r3 = incremental_aggregate(r2, far)
    far_clusters = [cl for cl in r3.l0_clusters if any(p.id.startswith("far") for p in cl)]
    assert len(far_clusters) == 1 and len(far_clusters[0]) == 3, "远处 3 张独立成新簇（超限才分裂）"
    # 旧照片全部保留（"先匹配"语义：簇只增不减、旧照片不丢；吸收后旧簇变大属预期）
    old_ids = {p.id for cl in r1.l0_clusters for p in cl}
    all_ids = {p.id for cl in r3.l0_clusters for p in cl}
    assert old_ids.issubset(all_ids), "旧照片全部保留（不漂移/不丢失）"


def test_llm_metadata_prompt_no_tag_candidate():
    """真实 LLM 元数据 prompt：无标签候选不崩溃（回归：join([None]) TypeError）"""
    from app.services.llm_ops.event_merge import _metadata_prompt

    c = {
        "tag": None, "tag_hint": [], "cluster": ["a", "b"],
        "time_range": ["2026-08-01T10:00:00+00:00", "2026-08-02T10:00:00+00:00"],
        "place_hint": "杭州市西湖区", "ocr_summary": None,
    }
    out = _metadata_prompt(c)
    assert "标签: （无）" in out, "无标签候选应渲染为（无），不抛 TypeError"
    assert "时间范围: 2026-08-01T10:00:00+00:00" in out
    # 混合：tag + tag_hint 去重并集
    c2 = {**c, "tag": "美食", "tag_hint": ["美食", "探店"]}
    out2 = _metadata_prompt(c2)
    assert "标签: 美食、探店" in out2, "tag 与 tag_hint 应合并展示"

