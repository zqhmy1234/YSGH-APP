#!/usr/bin/env python3
"""AGG-016 一致性夹具生成（Python 参考端 → UTS fixtures.uts）

同一组用例：Python（backend event_aggregation，同参）计算期望输出
（L0 簇成员 + L1 日卡片），生成 client/utils/agg/fixtures.uts 供端侧双跑比对。

2026-08-26 Wave2 AgentE 扩展：
  - 30min 保守模式场景（conservative=True → eps_t_sec=1800）——与 agg_config.uts
    CONSERVATIVE_MODE 开关、云侧 pipeline.py AGG_CONFIG 对齐（Agent D）
  - 预处理感知哈希去重场景（phash 重复 → 只保留首张）——与云端 uq_contents_user_hash 对齐

用法：
    python scripts/gen_agg_fixtures.py [--out client/utils/agg/fixtures.uts]

纪律（AGG-016）：
  - 双跑必须同参：tz_offset_minutes 固定 480（上海）；端侧 agg-check 页同参
  - 端侧参数单一来源：agg_config.uts ↔ pipeline.py AGG_CONFIG（改参数两端同步）
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent / "backend"
sys.path.insert(0, str(BACKEND_DIR))

from app.services.event_aggregation.pipeline import RawPhoto, preprocess  # noqa: E402
from app.services.event_aggregation.st_dbscan import l1_daily_aggregate, st_dbscan  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "client" / "utils" / "agg" / "fixtures.uts"

TZ_OFFSET_MIN = 480  # 上海（双跑同参）

# L0 参数（与 agg_config.uts / pipeline.py AGG_CONFIG 对齐；AGG-016 双跑同参）
EPS_T_DEFAULT = 3600.0     # 60min 宽窗（CONSERVATIVE_MODE=false）
EPS_T_CONSERVATIVE = 1800.0  # 30min 保守模式（CONSERVATIVE_MODE=true）
EPS_S_M = 500.0
MIN_PTS = 3


def local_ms(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> int:
    """本地墙钟（UTC+8 语义）→ epoch ms。fixture 时间按上海本地日界构造。"""
    dt = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc) - timedelta(minutes=TZ_OFFSET_MIN)
    return int(dt.timestamp() * 1000)


def _p(pid: str, ms: int, lat: float | None, lng: float | None, tags: list[str] | None = None) -> RawPhoto:
    return RawPhoto(id=pid, ts=datetime.fromtimestamp(ms / 1000, tz=timezone.utc), lat=lat, lng=lng, tags=tags or [])


def _dedup(photos: list[RawPhoto], phash: dict[str, str]) -> list[RawPhoto]:
    """① 感知哈希去重参考实现（与 UTS pipeline.dedup 同语义，AGG-016 双跑）

    key = phash 非空 ? phash : id；同 key 只保留首张。
    （云端 uq_contents_user_hash 语义：同用户同感知哈希只入库一次）
    """
    seen: set[str] = set()
    kept: list[RawPhoto] = []
    for p in photos:
        key = phash.get(p.id) or p.id
        if key in seen:
            continue
        seen.add(key)
        kept.append(p)
    return kept


def _expected(photos: list[RawPhoto], tz: int = TZ_OFFSET_MIN,
              conservative: bool = False, phash: dict[str, str | None] | None = None) -> dict:
    """Python 同参双跑：去重 → preprocess → st_dbscan → l1_daily_aggregate"""
    phash = phash or {}
    depped = _dedup(photos, phash)  # type: ignore[arg-type]
    pts = preprocess(depped)
    eps_t = EPS_T_CONSERVATIVE if conservative else EPS_T_DEFAULT
    clusters = st_dbscan(pts, eps_t_sec=eps_t, eps_s_m=EPS_S_M, min_pts=MIN_PTS)
    clustered_ids = {p.id for cl in clusters for p in cl}
    noise = [p for p in pts if p.id not in clustered_ids]
    days = l1_daily_aggregate(clusters, noise, tz_offset_minutes=tz)
    return {
        "clusters": [[p.id for p in cl] for cl in clusters],
        "days": [
            {"date": d["date"], "ids": [p.id for p in d["photos"]], "is_sparse": d["is_sparse"]}
            for d in days
        ],
    }


def build_cases() -> list[dict]:
    """用例集（每个用例注释说明意图；期望由 Python 同参计算，非手写）"""
    cases: list[dict] = []
    A = (31.2304, 121.4737)  # 上海人民广场
    B = (31.2306, 121.4740)  # ~30m 外
    FAR = (31.30, 121.50)    # ~9km 外

    # 1. 连拍折叠 + 同地点单簇
    t0 = local_ms(2026, 8, 24, 10, 0, 0)
    photos = [
        _p("p1", t0, *A, ["food"]),
        _p("p2", t0 + 2000, *A, ["food"]),
        _p("p3", t0 + 4000, *A, ["food"]),
        _p("p4", t0 + 10000, *A, ["food"]),
    ]
    cases.append({
        "name": "burst-fold-single-cluster", "tz": TZ_OFFSET_MIN,
        "photos": photos, "expected": _expected(photos),
    })

    # 2. 两天两簇（同地点跨天不合并：60min 时间窗）
    photos = [
        _p("p1", local_ms(2026, 8, 24, 10, 0), *A),
        _p("p2", local_ms(2026, 8, 24, 10, 5), *A),
        _p("p3", local_ms(2026, 8, 24, 10, 10), *A),
        _p("p4", local_ms(2026, 8, 25, 10, 0), *A),
        _p("p5", local_ms(2026, 8, 25, 10, 5), *A),
        _p("p6", local_ms(2026, 8, 25, 10, 10), *A),
    ]
    cases.append({
        "name": "two-clusters-two-days", "tz": TZ_OFFSET_MIN,
        "photos": photos, "expected": _expected(photos),
    })

    # 3. 散片并入日卡片（<min_pts，is_sparse）
    photos = [
        _p("p1", local_ms(2026, 8, 24, 9, 0), *A),
        _p("p2", local_ms(2026, 8, 24, 9, 5), *FAR),
    ]
    cases.append({"name": "noise-to-sparse-day", "tz": TZ_OFFSET_MIN, "photos": photos, "expected": _expected(photos)})

    # 4. 无 GPS 按时间窗归组
    t0 = local_ms(2026, 8, 24, 14, 0)
    photos = [
        _p("p1", t0, None, None, ["food"]),
        _p("p2", t0 + 600000, None, None, ["food"]),
        _p("p3", t0 + 1200000, None, None, ["food"]),
        _p("p4", t0 + 1800000, None, None, ["food"]),
    ]
    cases.append({"name": "no-gps-time-window", "tz": TZ_OFFSET_MIN, "photos": photos, "expected": _expected(photos)})

    # 5. 深夜归属前一天：23:40/23:50 + 次日 00:20/00:30 → 前一天；01:01 → 当天
    photos = [
        _p("p1", local_ms(2026, 8, 24, 23, 40), *A),
        _p("p2", local_ms(2026, 8, 24, 23, 50), *A),
        _p("p3", local_ms(2026, 8, 25, 0, 20), *A),
        _p("p4", local_ms(2026, 8, 25, 0, 30), *A),
        _p("p5", local_ms(2026, 8, 25, 1, 1), *A),
    ]
    cases.append({
        "name": "night-boundary-prev-day", "tz": TZ_OFFSET_MIN,
        "photos": photos, "expected": _expected(photos),
    })

    # 6. GPS 漂移修正：10s 内 100km → 坐标置空（仍按时间归组）
    t0 = local_ms(2026, 8, 24, 16, 0)
    photos = [
        _p("p1", t0, *A),
        _p("p2", t0 + 10000, 31.90, 122.10),  # ~100km，超速
        _p("p3", t0 + 20000, *B),
    ]
    cases.append({"name": "gps-drift-corrected", "tz": TZ_OFFSET_MIN, "photos": photos, "expected": _expected(photos)})

    # 7. 稀疏多天：每天 1 张 → 2 张日卡片（均 is_sparse），0 簇
    photos = [
        _p("p1", local_ms(2026, 8, 24, 8, 0), *A),
        _p("p2", local_ms(2026, 8, 25, 8, 0), *A),
    ]
    cases.append({"name": "sparse-multi-day", "tz": TZ_OFFSET_MIN, "photos": photos, "expected": _expected(photos)})

    # 8. 单张照片：0 簇 + 1 稀疏日卡片
    photos = [_p("p1", local_ms(2026, 8, 24, 12, 0), *A)]
    cases.append({"name": "single-photo", "tz": TZ_OFFSET_MIN, "photos": photos, "expected": _expected(photos)})

    # 9. UTC 日界（tz=0）：23:40 UTC 不触发深夜规则（本地=UTC 时无偏移）
    t0 = datetime(2026, 8, 24, 23, 40, tzinfo=timezone.utc)
    photos = [
        _p("p1", int(t0.timestamp() * 1000), *A),
        _p("p2", int((t0 + timedelta(minutes=10)).timestamp() * 1000), *A),
        _p("p3", int((t0 + timedelta(minutes=20)).timestamp() * 1000), *A),
    ]
    cases.append({"name": "utc-bucket-no-shift", "tz": 0, "photos": photos, "expected": _expected(photos, tz=0)})

    # 10. 规模用例：30 张跨 3 天 3 地点（簇/日卡片/散片混合）
    photos = []
    idx = 0
    for day in range(3):
        for spot_i, spot in enumerate([A, B, FAR]):
            for k in range(3):
                idx += 1
                photos.append(
                    _p(f"p{idx}", local_ms(2026, 8, 24 + day, 10 + spot_i, k * 2), *spot,
                       ["travel"] if day == 1 else None)
                )
    cases.append({
        "name": "scale-30-photos-3days", "tz": TZ_OFFSET_MIN,
        "photos": photos, "phash": {}, "conservative": False,
        "expected": _expected(photos),
    })

    # 11. 30min 保守模式开关（B3-2 · 2026-08-26 Wave2 AgentE）
    #     同地点两拨照片：10:00 3 张 + 10:45 3 张（间隔 45min）
    #     默认 60min 宽窗 → 1 簇 6 张；保守 30min → 2 簇 × 3 张
    t0 = local_ms(2026, 8, 24, 10, 0)
    photos = [
        _p("p1", t0, *A), _p("p2", t0 + 60000, *A), _p("p3", t0 + 120000, *A),
        _p("p4", t0 + 2700000, *A), _p("p5", t0 + 2760000, *A), _p("p6", t0 + 2820000, *A),
    ]
    cases.append({
        "name": "conservative-30min-split", "tz": TZ_OFFSET_MIN,
        "photos": photos, "phash": {}, "conservative": True,
        "expected": _expected(photos, conservative=True),
    })
    # 双跑兜底：同一输入默认模式（60min）必须是 1 簇——确认开关确实改变行为
    cases.append({
        "name": "default-60min-merge", "tz": TZ_OFFSET_MIN,
        "photos": photos, "phash": {}, "conservative": False,
        "expected": _expected(photos, conservative=False),
    })

    # 12. 预处理去重（感知哈希 · Q16，与云端 uq_contents_user_hash 对齐）
    #     p1/p2 同哈希、p3/p4 同哈希 → 去重保留 p1、p3 → 2 张散片 → 稀疏日卡片
    photos = [
        _p("p1", local_ms(2026, 8, 24, 12, 0), *A, ["food"]),
        _p("p2", local_ms(2026, 8, 24, 12, 5), *A, ["food"]),
        _p("p3", local_ms(2026, 8, 24, 12, 10), *A, ["food"]),
        _p("p4", local_ms(2026, 8, 24, 12, 15), *A, ["food"]),
    ]
    phash = {"p1": "a1b2c3d4", "p2": "a1b2c3d4", "p3": "e5f6a7b8", "p4": "e5f6a7b8"}
    cases.append({
        "name": "dedup-phash-duplicate", "tz": TZ_OFFSET_MIN,
        "photos": photos, "phash": phash, "conservative": False,
        "expected": _expected(photos, phash=phash),
    })

    # 13. 去重 + 连拍折叠组合：重复首张 + 间隔 100s 的第二组（跨出 5s 折叠窗）
    #     去重后 p1、p3 存活；100s 间隔不折叠 → 2 张散片 → 稀疏日卡片
    t0 = local_ms(2026, 8, 24, 13, 0)
    photos = [
        _p("p1", t0, *A),
        _p("p2", t0 + 2000, *A),
        _p("p3", t0 + 100000, *A),
        _p("p4", t0 + 102000, *A),
    ]
    phash = {"p1": "x1", "p2": "x1", "p3": "y2", "p4": "y2"}
    cases.append({
        "name": "dedup-burst-keep-first", "tz": TZ_OFFSET_MIN,
        "photos": photos, "phash": phash, "conservative": False,
        "expected": _expected(photos, phash=phash),
    })

    return cases


def _fmt_float(v: float | None) -> str:
    return "null" if v is None else repr(v)


def _fmt_tags(tags: list[str]) -> str:
    return "[" + ", ".join(f"'{t}'" for t in tags) + "]"


def render_uts(cases: list[dict]) -> str:
    lines: list[str] = []
    lines.append("/**")
    lines.append(" * AGG-016 一致性夹具（自动生成 · 勿手改）")
    lines.append(" * 来源：python scripts/gen_agg_fixtures.py（Python 同参双跑期望值）")
    lines.append(" */")
    lines.append("import { AggCase, AggExpected, AggExpectedDay, AggPhoto } from './agg_types.uts'")
    lines.append("")
    lines.append("export const AGG_FIXTURES: AggCase[] = [")
    for c in cases:
        phash = c.get("phash", {}) or {}
        conservative = bool(c.get("conservative", False))
        lines.append(f"\tnew AggCase('{c['name']}', {c['tz']}, [")
        for p in c["photos"]:
            tags = _fmt_tags(p.tags or [])
            ph = phash.get(p.id) or ""
            lines.append(
                f"\t\tnew AggPhoto('{p.id}', {int(p.ts.timestamp() * 1000)}, "
                f"{_fmt_float(p.lat)}, {_fmt_float(p.lng)}, {tags}, '{ph}'),"
            )
        lines.append("\t], new AggExpected([")
        for cl in c["expected"]["clusters"]:
            lines.append("\t\t[" + ", ".join(f"'{x}'" for x in cl) + "],")
        lines.append("\t], [")
        for d in c["expected"]["days"]:
            ids = ", ".join(f"'{x}'" for x in d["ids"])
            lines.append(f"\t\tnew AggExpectedDay('{d['date']}', [{ids}], {'true' if d['is_sparse'] else 'false'}),")
        lines.append(f"\t]), {str(conservative).lower()}),")
    lines.append("]")
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    cases = build_cases()
    out = args.out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_uts(cases), encoding="utf-8")
    total_photos = sum(len(c["photos"]) for c in cases)
    total_clusters = sum(len(c["expected"]["clusters"]) for c in cases)
    total_days = sum(len(c["expected"]["days"]) for c in cases)
    print(f"AGG-016 fixtures 生成完成: {len(cases)} 用例 / {total_photos} 照片 / "
          f"{total_clusters} 期望簇 / {total_days} 期望日卡片 → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
