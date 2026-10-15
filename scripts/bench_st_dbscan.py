"""ST-DBSCAN 剪枝优化版的「语义等价 + 性能」本地基准（2026-10-14 · 台账 §4.20 批次 5 · RC-7）。

## 为什么有它

`client/utils/agg/st_dbscan.uts` 是端侧 L0 聚类核心，而 `agg_runner` 在**上传完成回调里
同步调用**它（UTS 无 worker）。旧实现是双重全表扫描 ⇒ O(n²)，且每次判定都算 haversine——
n 一大就卡死 JS 线程。2026-10-14 做了**语义等价剪枝**（时间窗 + 空间盒预筛 + 候选顺序还原）。
"算法性能要保证"⇒ 既要有剪枝，也要有**可复现的等价性与量级证据**。

## 这个脚本做什么

1. **等价性**：把剪枝版按同一逻辑移植到 Python，与**仓库权威参考端**
   （`backend/app/services/event_aggregation/st_dbscan.py` —— AGG-016 双跑夹具的产出方）
   在「边界用例 + 随机模糊 + 大规模」语料上逐一对比，要求簇列表**逐位相同**
   （顺序敏感：DBSCAN 的边界点归属依赖遍历顺序）。
2. **性能**：同语料下测两版耗时，输出倍率。

## 诚实边界（必读）

· 本脚本里的剪枝版是 **Python 镜像**，不是 UTS 本身 ⇒ 它证明的是**算法等价性与量级**；
  端侧真等价以 AGG-016 双跑为准（`scripts/gen_agg_fixtures.py` 生成期望 → agg-check 页比对）。
· 镜像与 UTS 存在**漂移风险**：改 UTS 的剪枝逻辑后必须同步本文件，否则"绿"是假的。
  两边的关键常量与判定顺序已逐行对齐（`latDeg` / `LNG_MARGIN` / `+1ms 窗上浮` / 升序回排）。

用法：
    python scripts/bench_st_dbscan.py          # 等价性 + 性能表
    python scripts/bench_st_dbscan.py --quick  # 只跑小规模等价校验（秒级）
"""
from __future__ import annotations

import argparse
import importlib.util
import math
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
REF_PATH = REPO / "backend" / "app" / "services" / "event_aggregation" / "st_dbscan.py"


def _load_reference():
    """按路径加载参考端模块（它只依赖 math/dataclasses/datetime，可独立加载）。"""
    spec = importlib.util.spec_from_file_location("ref_st_dbscan", REF_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载参考端：{REF_PATH}")
    mod = importlib.util.module_from_spec(spec)
    # ⚠️ 必须先注册进 sys.modules：参考端用了 `@dataclass`，而 dataclasses 处理类时需要
    # `sys.modules[cls.__module__]`（不注册会报 AttributeError: NoneType has no __dict__）
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


REF = _load_reference()
Photo = REF.Photo
st_dbscan_reference = REF.st_dbscan

# ── 机器无关的代价计量：`isNeighbor` 调用数（其中走 haversine 的次数）──
# 为什么不用墙钟下结论：Python 的数组/循环比 UTS(JS) 慢一两个数量级，同地连拍那种
# 「大量数组构造但零 trig」的形态会被墙钟严重放大。真正决定设备耗时的是**判定次数**，
# 所以两边都挂同一计数（参考端内部按名调用 `_is_neighbor` ⇒ 替换模块属性即可全量计数）。
_ORIG_IS_NEIGHBOR = REF._is_neighbor
_COUNTS = {"calls": 0, "haversine": 0}


def _counted_is_neighbor(a, b, eps_t_sec, eps_s_m) -> bool:
    _COUNTS["calls"] += 1
    if a.lat is not None and a.lng is not None and b.lat is not None and b.lng is not None:
        _COUNTS["haversine"] += 1
    return _ORIG_IS_NEIGHBOR(a, b, eps_t_sec, eps_s_m)


REF._is_neighbor = _counted_is_neighbor  # noqa: SLF001 —— 计量探针（仅基准用）


# ───────────────────────── 剪枝版（与 UTS 逐行对齐）─────────────────────────

LAT_M_PER_DEG = 110574.0   # 每度纬度下界（赤道）⇒ 度阈值最大 ⇒ 盒最大（保守）
LNG_MARGIN = 1.01          # 经度盒余量（窗内 |Δlat| ≤ latDeg，cos 变化 <1e-3%）
EPS_T_MS_FLOAT = 1.0       # 时间窗上浮 1ms：窗只需是超集，|Δt| 判定仍在 _is_neighbor 内


def st_dbscan_pruned(photos: list, eps_t_sec: float, eps_s_m: float, min_pts: int) -> list:
    n = len(photos)
    clusters: list = []
    if n == 0:
        return clusters

    visited = [False] * n
    ts_ms = [p.ts.timestamp() * 1000.0 for p in photos]

    order = sorted(range(n), key=lambda i: ts_ms[i])
    rank = [0] * n
    for r, i in enumerate(order):
        rank[i] = r

    eps_t_ms = eps_t_sec * 1000.0 + EPS_T_MS_FLOAT
    win_lo = [0] * n
    win_hi = [n - 1] * n
    lo = 0
    hi = 0
    for r in range(n):
        t = ts_ms[order[r]]
        while ts_ms[order[lo]] < t - eps_t_ms:
            lo += 1
        if hi < r:
            hi = r
        while hi + 1 < n and ts_ms[order[hi + 1]] <= t + eps_t_ms:
            hi += 1
        win_lo[r] = lo
        win_hi[r] = hi

    lat_deg = eps_s_m / LAT_M_PER_DEG

    def try_clique(i: int) -> list | None:
        """团快路（与 UTS 逐行对齐）：可证明两两皆邻居 ⇒ 返回升序全集，否则 None。"""
        r = rank[i]
        its = ts_ms[i]
        min_ts = max_ts = its
        min_lat, max_lat = 90.0, -90.0
        min_lng, max_lng = 180.0, -180.0
        any_gps = False
        a0 = photos[i]
        if a0.lat is not None and a0.lng is not None:
            min_lat = max_lat = a0.lat
            min_lng = max_lng = a0.lng
            any_gps = True
        out: list[int] = []
        for k in range(win_lo[r], win_hi[r] + 1):
            j = order[k]
            if j == i:
                continue
            b0 = photos[j]
            min_ts = min(min_ts, ts_ms[j])
            max_ts = max(max_ts, ts_ms[j])
            if b0.lat is not None and b0.lng is not None:
                if not any_gps:
                    min_lat = max_lat = b0.lat
                    min_lng = max_lng = b0.lng
                    any_gps = True
                else:
                    min_lat = min(min_lat, b0.lat)
                    max_lat = max(max_lat, b0.lat)
                    min_lng = min(min_lng, b0.lng)
                    max_lng = max(max_lng, b0.lng)
            out.append(j)
        if max_ts - min_ts > eps_t_sec * 1000.0 - 1.0:
            return None
        if any_gps:
            abs_min = min(abs(min_lat), abs(max_lat))
            cos_ref = max(0.01, math.cos(math.radians(abs_min)))
            if max_lat - min_lat > lat_deg * 0.49:
                return None
            if max_lng - min_lng > lat_deg / cos_ref * 0.49:
                return None
        out.sort()
        return out

    def collect(i: int) -> list:
        fast = try_clique(i)
        if fast is not None:
            return fast
        a = photos[i]
        r = rank[i]
        has_a = a.lat is not None and a.lng is not None
        cos_lat = math.cos(math.radians(a.lat)) if has_a else 1.0
        if cos_lat < 0.01:
            cos_lat = 0.01
        lng_deg = lat_deg / cos_lat * LNG_MARGIN
        out: list[int] = []
        for k in range(win_lo[r], win_hi[r] + 1):
            j = order[k]
            if j == i:
                continue
            if has_a:
                b = photos[j]
                if b.lat is not None and b.lng is not None:
                    if abs(b.lat - a.lat) > lat_deg:
                        continue
                    dl = abs(b.lng - a.lng)
                    if dl > 180.0:
                        dl = 360.0 - dl
                    if dl > lng_deg:
                        continue
            if REF._is_neighbor(a, photos[j], eps_t_sec, eps_s_m):
                out.append(j)
        out.sort()
        return out

    for i in range(n):
        if visited[i]:
            continue
        visited[i] = True
        seed = photos[i]
        neighbors = collect(i)
        if len(neighbors) < min_pts - 1:
            continue
        cluster = [seed]
        queue = list(neighbors)
        while queue:
            j = queue.pop()
            if visited[j]:
                continue
            visited[j] = True
            cluster.append(photos[j])
            jn = collect(j)
            if len(jn) >= min_pts - 1:
                queue.extend(k for k in jn if not visited[k])
        clusters.append(cluster)
    return clusters


# ───────────────────────── 语料 ─────────────────────────

BASE = datetime(2026, 1, 1)


def make_photos(specs: list[tuple[str, datetime, float | None, float | None]]) -> list:
    return [Photo(pid, ts, lat, lng, None, None) for pid, ts, lat, lng in specs]


def corpus_outings(n: int, seed: int = 0, no_gps_ratio: float = 0.15) -> list:
    """模拟"若干次外出"：每次 1~40 张、位置集中（±450m）、时间集中（3h 内）。"""
    rnd = random.Random(seed)
    specs: list[tuple[str, datetime, float | None, float | None]] = []
    i = 0
    while i < n:
        k = min(n - i, rnd.randint(1, 40))
        t0 = BASE + timedelta(days=rnd.randint(0, 364), hours=rnd.randint(0, 23), minutes=rnd.randint(0, 59))
        lat0, lng0 = rnd.uniform(20, 45), rnd.uniform(100, 125)
        for j in range(k):
            ts = t0 + timedelta(seconds=rnd.randint(0, 3 * 3600))
            if rnd.random() < no_gps_ratio:
                specs.append((f"p{i + j}", ts, None, None))
            else:
                specs.append((f"p{i + j}", ts, lat0 + rnd.gauss(0, 0.004), lng0 + rnd.gauss(0, 0.004)))
        i += k
    return make_photos(specs)


def corpus_dense_burst(n: int) -> list:
    """同地同时段 n 张（时间窗与空间盒都不剪枝 ⇒ 量级下界由它决定）。

    ⚠️ 生产入口**不是**本函数：真实输入先过 `pipeline.preprocess` 的**连拍折叠**
    （间隔 <5s 折叠为 1 个时间点），故"同一地点、同一 60min 窗"内可达点数上界
    ≈ **3600 / 5 = 720**。n 超过 720 属**不可达输入**，此处只用于暴露量级形状。
    """
    t0 = BASE
    return make_photos([(f"b{i}", t0 + timedelta(seconds=i * 5), 31.23 + (i % 7) * 0.0001, 121.47) for i in range(n)])


def corpus_burst_reachable() -> list:
    """**折叠后可达的最坏形态**：同一地点、5s 一张、满 60min 窗 ⇒ 720 点。

    这才是"算法性能要保证"真正需要承诺的那个输入（推导见 `corpus_dense_burst` 注释）。
    """
    return corpus_dense_burst(720)


def corpus_spread(n: int) -> list:
    """一年内均匀散开（真实相册的常态）：时间窗剪枝收益最大。"""
    return make_photos([(f"s{i}", BASE + timedelta(minutes=i * 60), 30.0 + (i % 50) * 0.01, 120.0) for i in range(n)])


def corpus_cases() -> list[tuple[str, list, float, float, int]]:
    """边界与异常用例（等价性必须全部逐位一致）。"""
    t0 = BASE
    eps_t, eps_s, min_pts = 3600.0, 500.0, 3
    cases: list[tuple[str, list, float, float, int]] = []
    cases.append(("空", [], eps_t, eps_s, min_pts))
    cases.append(("单点", make_photos([("a", t0, 31.0, 121.0)]), eps_t, eps_s, min_pts))
    cases.append(("两点同刻",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0, 31.0001, 121.0001)]), eps_t, eps_s, min_pts))
    cases.append(("全同时刻",
                  make_photos([(f"x{i}", t0, 31.0, 121.0) for i in range(6)]), eps_t, eps_s, min_pts))
    cases.append(("全无 GPS 同窗",
                  make_photos([(f"n{i}", t0 + timedelta(seconds=i * 60), None, None) for i in range(6)]),
                  eps_t, eps_s, min_pts))
    cases.append(("混合无 GPS",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0 + timedelta(seconds=30), None, None),
                               ("c", t0 + timedelta(seconds=60), 31.0005, 121.0005)]), eps_t, eps_s, min_pts))
    cases.append(("时间窗边界（差 3600s 整）",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0 + timedelta(seconds=3600), 31.0, 121.0)]),
                  eps_t, eps_s, min_pts))
    cases.append(("时间窗外 1s",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0 + timedelta(seconds=3601), 31.0, 121.0)]),
                  eps_t, eps_s, min_pts))
    # 500m 边界：±0.0045° 纬度 ≈ 500m
    cases.append(("距离边界内（≈499m）",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0, 31.00448, 121.0), ("c", t0, 31.0, 121.0)]),
                  eps_t, eps_s, min_pts))
    cases.append(("距离边界外（≈505m）",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0, 31.00454, 121.0), ("c", t0, 31.0, 121.0)]),
                  eps_t, eps_s, min_pts))
    cases.append(("跨 ±180 经线",
                  make_photos([("a", t0, 5.0, 179.9999), ("b", t0, 5.0, -179.9999), ("c", t0, 5.0, 179.9998)]),
                  eps_t, eps_s, min_pts))
    cases.append(("两簇相距 5km",
                  make_photos([("a1", t0, 31.0, 121.0), ("a2", t0, 31.0002, 121.0002), ("a3", t0, 31.0001, 121.0),
                               ("b1", t0 + timedelta(minutes=1), 31.05, 121.0),
                               ("b2", t0 + timedelta(minutes=1), 31.0502, 121.0002),
                               ("b3", t0 + timedelta(minutes=1), 31.0501, 121.0)]), eps_t, eps_s, min_pts))
    cases.append(("minPts=2（更易成簇）",
                  make_photos([("a", t0, 31.0, 121.0), ("b", t0, 31.0001, 121.0)]), eps_t, eps_s, 2))
    # ── 团快路（充分条件）边界：只要界的推导有误，逐位对比必红 ──
    lat_step = 500.0 / 110574.0   # 1 个 εs 对应的纬度度数
    lng_step = lat_step / 0.857   # 与之等距的经度度数（北纬 31°，cos≈0.857）
    cases.append(("团快路·半盒内（应生效）",
                  make_photos([(f"d{i}", t0 + timedelta(seconds=i * 10),
                                31.0 + (i % 3) * 0.45 * lat_step,
                                121.0 + (i % 4) * 0.45 * lng_step) for i in range(9)]),
                  eps_t, eps_s, min_pts))
    cases.append(("团快路·一维越界（经度铺开）",
                  make_photos([(f"c{i}", t0, 31.0, 121.0 + i * lng_step) for i in range(5)]),
                  eps_t, eps_s, min_pts))
    cases.append(("团快路·时间跨度超窗（2h）",
                  make_photos([(f"e{i}", t0 + timedelta(seconds=i * 1800), 31.0, 121.0) for i in range(5)]),
                  eps_t, eps_s, min_pts))
    cases.append(("团快路·GPS 混合",
                  make_photos([("m0", t0, 31.0, 121.0), ("m1", t0 + timedelta(seconds=5), None, None),
                               ("m2", t0 + timedelta(seconds=10), 31.0001, 121.0001),
                               ("m3", t0 + timedelta(seconds=15), 31.0002, 121.0002)]),
                  eps_t, eps_s, min_pts))
    cases.append(("团快路·对角铺满半盒",
                  make_photos([("g0", t0, 31.0, 121.0), ("g1", t0, 31.0 + 0.49 * lat_step, 121.0),
                               ("g2", t0, 31.0, 121.0 + 0.49 * lng_step),
                               ("g3", t0, 31.0 + 0.49 * lat_step, 121.0 + 0.49 * lng_step)]),
                  eps_t, eps_s, min_pts))
    cases.append(("极区（cos 保护）",
                  make_photos([("a", t0, 89.9, 10.0), ("b", t0, 89.9001, 10.0001), ("c", t0, 89.9002, 10.0)]),
                  eps_t, eps_s, min_pts))
    return cases


def fuzz_cases(count: int, seed: int = 7) -> list[tuple[str, list, float, float, int]]:
    rnd = random.Random(seed)
    out = []
    for c in range(count):
        n = rnd.randint(0, 60)
        spread = rnd.choice([0.0005, 0.005, 0.05])
        ts_span = rnd.choice([60, 1800, 7200, 86400])
        no_gps = rnd.choice([0.0, 0.2, 0.5, 1.0])
        specs = []
        for i in range(n):
            ts = BASE + timedelta(seconds=rnd.randint(0, ts_span))
            if rnd.random() < no_gps:
                specs.append((f"f{c}_{i}", ts, None, None))
            else:
                specs.append((f"f{c}_{i}", ts, 31.0 + rnd.gauss(0, spread), 121.0 + rnd.gauss(0, spread)))
        out.append((f"fuzz#{c}(n={n})", make_photos(specs),
                    rnd.choice([60.0, 1800.0, 3600.0]), rnd.choice([200.0, 500.0, 2000.0]),
                    rnd.choice([2, 3, 4])))
    return out


def _sig(clusters: list) -> list:
    return [[p.id for p in c] for c in clusters]


def run_equivalence(cases: list, verbose: bool) -> tuple[int, list[str]]:
    """逐位比较；返回 (失败数, 失败明细)。"""
    fails: list[str] = []
    for name, photos, eps_t, eps_s, min_pts in cases:
        ref = _sig(st_dbscan_reference(photos, eps_t, eps_s, min_pts))
        got = _sig(st_dbscan_pruned(photos, eps_t, eps_s, min_pts))
        if ref != got:
            fails.append(f"{name}: 参考={ref} 剪枝={got}")
        elif verbose:
            print(f"  [ok] {name}  簇数={len(ref)}")
    return len(fails), fails


def bench(label: str, photos: list, eps_t: float, eps_s: float, min_pts: int,
          with_reference: bool) -> None:
    n = len(photos)
    _COUNTS["calls"] = 0
    _COUNTS["haversine"] = 0
    t = time.perf_counter()
    got = st_dbscan_pruned(photos, eps_t, eps_s, min_pts)
    t_pruned = time.perf_counter() - t
    p_calls, p_hav = _COUNTS["calls"], _COUNTS["haversine"]
    line = (f"  {label:<30} n={n:<5} 判定 {p_calls:<8} 含hav {p_hav:<8} "
            f"{t_pruned * 1000:8.1f}ms 簇={len(got)}")
    if with_reference:
        _COUNTS["calls"] = 0
        _COUNTS["haversine"] = 0
        t = time.perf_counter()
        ref = st_dbscan_reference(photos, eps_t, eps_s, min_pts)
        t_ref = time.perf_counter() - t
        r_calls, r_hav = _COUNTS["calls"], _COUNTS["haversine"]
        same = _sig(ref) == _sig(got)
        line += (f" ｜ 全扫 判定 {r_calls:<8} 含hav {r_hav:<8} {t_ref * 1000:8.1f}ms"
                 f"  一致={'✓' if same else '✗!!'}")
    print(line)


def main() -> int:
    ap = argparse.ArgumentParser(description="ST-DBSCAN 剪枝版等价性 + 性能基准")
    ap.add_argument("--quick", action="store_true", help="只跑小规模等价校验")
    args = ap.parse_args()

    print("== 1. 边界与异常用例（逐位等价）==")
    cases = corpus_cases()
    n_fail, fails = run_equivalence(cases, verbose=True)
    print(f"  边界用例 {len(cases)}：失败 {n_fail}")

    print("== 2. 随机模糊（逐位等价，多种 ε/无 GPS 比例）==")
    fz = fuzz_cases(120 if not args.quick else 20)
    n_fail2, fails2 = run_equivalence(fz, verbose=False)
    print(f"  模糊用例 {len(fz)}：失败 {n_fail2}")

    all_fails = fails + fails2
    if all_fails:
        print("!! 等价性失败：")
        for f in all_fails[:10]:
            print("   - " + f)
        return 1

    if args.quick:
        print("== 等价性全部通过（quick 模式跳过性能表）==")
        return 0

    print("== 3. 性能（真实形态语料）==")
    eth, esm, mp = 3600.0, 500.0, 3
    for n in (500, 1000, 1500):
        bench("一年内均匀散开", corpus_spread(n), eth, esm, mp, with_reference=True)
    for n in (500, 1000, 1500):
        bench("多次外出（每次≤40张）", corpus_outings(n, seed=n), eth, esm, mp, with_reference=True)
    # 最坏形态：只测**折叠后可达上界**（720）；超上界的输入在生产不可达，仅看形状不跑全扫基准
    bench("最坏形态·可达上界（折叠后 720）", corpus_burst_reachable(), eth, esm, mp, with_reference=True)
    bench("超可达上界 n=1000（不可达输入·仅形状）", corpus_dense_burst(1000), eth, esm, mp, with_reference=False)

    print("== 4. 大规模（仅剪枝版：全扫在此规模下已不适合作为基准）==")
    for n in (4000, 8000):
        bench("一年内均匀散开", corpus_spread(n), eth, esm, mp, with_reference=False)
    for n in (4000, 8000):
        bench("多次外出（每次≤40张）", corpus_outings(n, seed=n), eth, esm, mp, with_reference=False)

    print("== 等价性全部通过；性能见上表 ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
