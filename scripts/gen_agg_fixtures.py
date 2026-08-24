#!/usr/bin/env python3
"""AGG-016 一致性夹具生成（Python 参考端 → UTS fixtures.uts）

同一组用例：Python（backend event_aggregation，同参）计算期望输出
（L0 簇成员 + L1 日卡片），生成 client/utils/agg/fixtures.uts 供端侧双跑比对。

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


def local_ms(y: int, m: int, d: int, hh: int, mm: int, ss: int = 0) -> int:
    """本地墙钟（UTC+8 语义）→ epoch ms。fixture 时间按上海本地日界构造。"""
    dt = datetime(y, m, d, hh, mm, ss, tzinfo=timezone.utc) - timedelta(minutes=TZ_OFFSET_MIN)
    return int(dt.timestamp() * 1000)


def _p(pid: str, ms: int, lat: float | None, lng: float | None, tags: list[str] | None = None) -> RawPhoto:
    return RawPhoto(id=pid, ts=datetime.fromtimestamp(ms / 1000, tz=timezone.utc), lat=lat, lng=lng, tags=tags or [])


def _expected(photos: list[RawPhoto], tz: int = TZ_OFFSET_MIN) -> dict:
    """Python 同参双跑：preprocess → st_dbscan → l1_daily_aggregate"""
    pts = preprocess(photos)
    clusters = st_dbscan(pts, eps_t_sec=3600.0, eps_s_m=500.0, min_pts=3)
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
        "photos": photos, "expected": _expected(photos),
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
        lines.append(f"\tnew AggCase('{c['name']}', {c['tz']}, [")
        for p in c["photos"]:
            tags = _fmt_tags(p.tags or [])
            lines.append(
                f"\t\tnew AggPhoto('{p.id}', {int(p.ts.timestamp() * 1000)}, "
                f"{_fmt_float(p.lat)}, {_fmt_float(p.lng)}, {tags}),"
            )
        lines.append("\t], new AggExpected([")
        for cl in c["expected"]["clusters"]:
            lines.append("\t\t[" + ", ".join(f"'{x}'" for x in cl) + "],")
        lines.append("\t], [")
        for d in c["expected"]["days"]:
            ids = ", ".join(f"'{x}'" for x in d["ids"])
            lines.append(f"\t\tnew AggExpectedDay('{d['date']}', [{ids}], {'true' if d['is_sparse'] else 'false'}),")
        lines.append("\t])),")
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
