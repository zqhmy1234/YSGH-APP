#!/usr/bin/env python3
"""忆述光华 · 并发压测主脚本（Agent C1，MVP 收尾 · 性能压测域）

选型理由（报告 §2 注明）：
  - httpx.AsyncClient + asyncio.Semaphore 控制并发——零新重依赖、结果可控、CI/本地一致；
    不用 locust（避免引入重依赖与分布式协调成本）。
  - 计时口径：客户端侧（请求发出→响应完成，真实用户体验口径），不含服务端内部计时。

测什么（4 条既有 openapi 路径，无新契约）：
  P1 auth     POST /api/v1/auth/wechat      —— 认证链路（dev mock 通道，code 池轮换）
  P2 timeline GET  /api/v1/events/timeline  —— 核心读路径（聚合 + 计数批量）
  P3 search   POST /api/v1/search           —— 描述性检索（召回+重排+LLM 精排，门禁路径）
  P4 image    POST /api/v1/search/image     —— 以图搜图（caption 缓存命中/未命中两态）

运行：
  python loadtest.py --mode mock --levels 1,10,50,100            # mock 档全梯度
  python loadtest.py --mode real --levels 100 --tag real_20260828  # 真实档 100 并发
  可选：--probe 追加 200 并发探测拐点（报告标注"参考"）；--pid <后端进程id> 采样 CPU/内存。

前置（README 有完整说明）：
  - Docker redis/qdrant + PG 可用；后端已启动（uvicorn app.main:app --port 8000，
    压测态限流放宽见 README）；种子数据已就绪（python seed_data.py）。

产物：
  results/run_<tag>_<level>_<path>.json   —— 每档完整统计
  results/summary_<tag>.csv               —— 路径 × 档 分位/QPS 汇总
  results/p95_curve_<tag>.png             —— P95 曲线（X=并发档）
  results/qps_curve_<tag>.png             —— QPS 曲线
  results/resource_<tag>.csv              —— 后端进程资源采样（--pid 时）
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = Path(__file__).resolve().parent
RESULTS = HERE / "results"
RESULTS.mkdir(parents=True, exist_ok=True)
ASSETS = HERE / "assets"

# 429 业务码（ratelimit.py RATE_LIMIT_CODE）
_RATE_LIMITED_CODE = "RATE_LIMITED"


# ---------------------------------------------------------------------------
# 配置加载
# ---------------------------------------------------------------------------


def _load_config() -> dict:
    with open(HERE / "config.json", encoding="utf-8") as f:
        return json.load(f)


def _default_tag(mode: str) -> str:
    return f"{mode}_{datetime.now(timezone.utc):%Y%m%d}"


# ---------------------------------------------------------------------------
# 统计
# ---------------------------------------------------------------------------


def _percentile(sorted_lats: list[float], p: float) -> float:
    if not sorted_lats:
        return 0.0
    idx = min(len(sorted_lats) - 1, int(p * len(sorted_lats)))
    return round(sorted_lats[idx], 2)


def compute_stats(samples: list[dict], timeouts: int, conn_errors: int, elapsed: float) -> dict:
    """samples: [{lat_ms, status, ts}]；超时/连接错误单列，不计入延迟分位。

    total = 延迟样本 + 超时 + 连接错误；429 单列不计入错误率。
    """
    lats = sorted(s["lat_ms"] for s in samples)
    ok = sum(1 for s in samples if 200 <= s["status"] < 300)
    err = sum(1 for s in samples if not (200 <= s["status"] < 300) and s["status"] != 429)
    rl = sum(1 for s in samples if s["status"] == 429)
    worst = max(samples, key=lambda s: s["lat_ms"]) if samples else None
    n = len(lats)
    total = n + timeouts + conn_errors
    errors = err + conn_errors
    return {
        "samples": n,
        "p50_ms": _percentile(lats, 0.50),
        "p90_ms": _percentile(lats, 0.90),
        "p95_ms": _percentile(lats, 0.95),
        "p99_ms": _percentile(lats, 0.99),
        "avg_ms": round(sum(lats) / n, 2) if n else 0.0,
        "max_ms": round(worst["lat_ms"], 2) if worst else 0.0,
        "max_ts": worst["ts"] if worst else "",
        "qps": round(total / elapsed, 2) if elapsed > 0 else 0.0,
        "total": total,
        "ok": ok,
        "errors": errors,
        "rate_limited": rl,
        "timeouts": timeouts,
        "error_rate": round(errors / total, 6) if total else 0.0,
        "success_rate": round(ok / total, 6) if total else 0.0,
        "timeout_rate": round(timeouts / total, 6) if total else 0.0,
        "rate_limited_rate": round(rl / total, 6) if total else 0.0,
        "elapsed_s": round(elapsed, 3),
    }


def _print_table(stats: dict, path: str, level: int, mode: str) -> None:
    print(
        f"[{mode}] {path:8s} L{level:<4d} n={stats['samples']:<4d} "
        f"P50={stats['p50_ms']:>7.1f} P90={stats['p90_ms']:>7.1f} P95={stats['p95_ms']:>7.1f} "
        f"P99={stats['p99_ms']:>8.1f} AVG={stats['avg_ms']:>7.1f} MAX={stats['max_ms']:>8.1f} ms | "
        f"QPS={stats['qps']:>6.2f} 成功率={stats['success_rate']:.4f} "
        f"错误率={stats['error_rate']:.4f} 超时率={stats['timeout_rate']:.5f} 429占比={stats['rate_limited_rate']:.5f}"
    )


# ---------------------------------------------------------------------------
# 请求变体（路径 × 参数轮换）
# ---------------------------------------------------------------------------


def _build_variant_generator(path_cfg: dict, cfg: dict):
    """返回一个无限轮换的变体生成器（每次 next() 给一个请求参数 dict）"""
    kind = path_cfg["kind"]
    params = path_cfg["params"]
    if kind == "json":
        if "codes" in params:  # auth：code 池轮换
            codes = params["codes"]
            dev = params.get("device_id", "loadtest-p1-dev")
            i = 0

            def gen():
                nonlocal i
                code = codes[i % len(codes)]
                i += 1
                return {"json": {"code": code, "device_id": dev}}

            return gen
        if "query_words" in params:  # search：查询词库轮换
            words = params["query_words"]
            limit = params.get("limit", 20)
            j = 0

            def gen():
                nonlocal j
                word = words[j % len(words)]
                j += 1
                return {"json": {"q": word, "limit": limit}}

            return gen
        raise ValueError(f"未知 json 参数: {params}")
    if kind == "query":
        qp = dict(params)
        return lambda: {"params": qp}
    if kind == "multipart":
        images = [ASSETS / n for n in params["query_images"]]
        limit = params.get("limit", 10)
        blobs = []
        for img in images:
            if not img.exists():
                raise FileNotFoundError(f"查询图缺失（先跑 python seed_data.py）: {img}")
            blobs.append((img.name, img.read_bytes()))
        k = 0

        def gen():
            nonlocal k
            name, data = blobs[k % len(blobs)]
            k += 1
            return {"files": {"file": (name, data, "image/jpeg")}, "params": {"limit": limit}}

        return gen
    raise ValueError(f"未知 kind: {kind}")


# ---------------------------------------------------------------------------
# 单路径单档：warmup → timed run
# ---------------------------------------------------------------------------


async def _warmup(client: httpx.AsyncClient, path_cfg: dict, headers: dict, gen, cfg: dict) -> list[float]:
    """预热（不计入统计）：每路径 10 次；image 每张查询图 1 次（caption 缓存填充）"""
    kind = path_cfg["kind"]
    count = path_cfg.get("warmup_images", 0) if kind == "multipart" else 10
    lats: list[float] = []
    for _ in range(max(count, 1)):
        variant = gen()
        method = path_cfg["method"]
        url = path_cfg["url"]
        t0 = time.perf_counter()
        try:
            await client.request(method, url, headers=headers, **variant)
        except httpx.HTTPError:
            pass
        lats.append((time.perf_counter() - t0) * 1000)
    return lats


async def _timed_run(
    client: httpx.AsyncClient,
    path_cfg: dict,
    headers: dict,
    gen,
    cfg: dict,
    level: int,
) -> tuple[list[dict], int, int, float]:
    """timed run：level 并发 × (duration 或 min_requests 先到者)

    返回 (samples, timeouts, conn_errors, elapsed)；超时/连接错误不计入延迟分位。
    """
    duration = cfg["duration_seconds_high"] if level >= 100 else cfg["duration_seconds"]
    min_req = cfg["min_requests_per_level"].get(str(level), cfg["min_requests"])

    sem = asyncio.Semaphore(level)
    samples: list[dict] = []
    state = {"done": False, "completed": 0, "timeouts": 0, "conn_errors": 0}
    start = time.perf_counter()

    async def worker() -> None:
        while not state["done"]:
            async with sem:
                if state["done"]:
                    return
                variant = gen()
                method = path_cfg["method"]
                url = path_cfg["url"]
                t0 = time.perf_counter()
                try:
                    resp = await client.request(method, url, headers=headers, **variant)
                    lat = (time.perf_counter() - t0) * 1000
                    samples.append({"lat_ms": lat, "status": resp.status_code, "ts": time.strftime("%H:%M:%S")})
                except httpx.TimeoutException:
                    state["timeouts"] += 1  # 超时：计入超时率，不进延迟分位
                except httpx.HTTPError:
                    state["conn_errors"] += 1  # 连接类错误：计入错误率，不进延迟分位
                state["completed"] += 1
                if state["completed"] >= min_req or (time.perf_counter() - start) >= duration:
                    state["done"] = True

    workers = [asyncio.create_task(worker()) for _ in range(level)]
    await asyncio.gather(*workers)
    elapsed = time.perf_counter() - start
    return samples, state["timeouts"], state["conn_errors"], elapsed


async def run_path(
    client: httpx.AsyncClient,
    path_name: str,
    path_cfg: dict,
    seed_headers: dict,
    cfg: dict,
    level: int,
    mode: str,
    tag: str,
) -> dict:
    headers = seed_headers if path_cfg.get("use_seed_token") else {}
    gen = _build_variant_generator(path_cfg, cfg)

    warm = await _warmup(client, path_cfg, headers, gen, cfg)
    samples, timeouts, conn_errors, elapsed = await _timed_run(client, path_cfg, headers, gen, cfg, level)
    stats = compute_stats(samples, timeouts, conn_errors, elapsed)
    stats["path"] = path_name
    stats["level"] = level
    stats["mode"] = mode
    stats["tag"] = tag
    stats["warmup_latencies_ms"] = [round(x, 2) for x in warm]
    stats["rate_limit_note"] = cfg["rate_limit_note"]

    out = RESULTS / f"run_{tag}_{level}_{path_name}.json"
    out.write_text(json.dumps(stats, ensure_ascii=False, indent=2), encoding="utf-8")
    _print_table(stats, path_name, level, mode)
    return stats


# ---------------------------------------------------------------------------
# 资源采样（可选，--pid）
# ---------------------------------------------------------------------------


class ResourceSampler:
    def __init__(self, pid: int, path: Path) -> None:
        self.pid = pid
        self.path = path
        self._stop = threading.Event()
        self._rows: list[list] = []
        self._t = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        import psutil

        try:
            proc = psutil.Process(self.pid)
        except psutil.NoSuchProcess:
            print(f"[resource] ⚠️ 进程 {self.pid} 不存在，跳过资源采样")
            return
        while not self._stop.is_set():
            try:
                cpu = proc.cpu_percent(interval=None) / os.cpu_count()
                mem = proc.memory_info().rss / 1024 / 1024
                self._rows.append([time.strftime("%H:%M:%S"), round(cpu, 1), round(mem, 1)])
            except psutil.NoSuchProcess:
                break
            time.sleep(2)

    def start(self) -> None:
        self._t.start()

    def stop(self) -> None:
        self._stop.set()
        self._t.join(timeout=3)
        if self._rows:
            with open(self.path, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["ts", "cpu_pct", "rss_mb"])
                w.writerows(self._rows)
            print(f"[resource] 采样 {len(self._rows)} 行 → {self.path}")


# ---------------------------------------------------------------------------
# 曲线
# ---------------------------------------------------------------------------


def _plot_curves(summary_path: Path, tag: str, mode: str) -> None:
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt

        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Arial"]
        plt.rcParams["axes.unicode_minus"] = False
    except Exception as exc:  # noqa: BLE001 —— 无 matplotlib 则仅文本分位表（已打印）
        print(f"[plot] matplotlib 不可用，仅产出文本分位表: {exc}")
        return

    rows = list(csv.DictReader(open(summary_path, encoding="utf-8")))
    if not rows:
        return
    paths = sorted({r["path"] for r in rows})

    def series(key: str):
        return {
            p: [(int(r["level"]), float(r[key])) for r in rows if r["path"] == p]
            for p in paths
        }

    for key, fname, ylabel in [
        ("p95_ms", f"p95_curve_{tag}.png", "P95 latency (ms)"),
        ("qps", f"qps_curve_{tag}.png", "QPS"),
    ]:
        fig, ax = plt.subplots(figsize=(7, 5))
        for p in paths:
            pts = sorted(series(key)[p])
            if pts:
                xs = [x for x, _ in pts]
                ys = [y for _, y in pts]
                ax.plot(xs, ys, marker="o", label=p)
        ax.set_xlabel("concurrency")
        ax.set_ylabel(ylabel)
        ax.set_title(f"{key} vs concurrency ({mode})")
        ax.legend()
        ax.grid(True, alpha=0.3)
        fig.tight_layout()
        fig.savefig(RESULTS / fname, dpi=120)
        print(f"[plot] → {RESULTS / fname}")
        plt.close(fig)


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def _login_sync(base_url: str, code: str, device_id: str) -> str:
    with httpx.Client(timeout=10) as c:
        r = c.post(f"{base_url}/api/v1/auth/wechat", json={"code": code, "device_id": device_id})
        r.raise_for_status()
        return r.json()["data"]["access_token"]


def main() -> int:
    parser = argparse.ArgumentParser(description="忆述光华并发压测")
    parser.add_argument("--mode", choices=["mock", "real"], required=True, help="mock档 / 真实档（标注用）")
    parser.add_argument("--levels", default=None, help="并发梯度逗号分隔，如 1,10,50,100")
    parser.add_argument("--probe", action="store_true", help="追加 200 并发探测拐点（标注参考）")
    parser.add_argument("--tag", default=None, help="结果标签（默认 mock_yyyymmdd / real_yyyymmdd）")
    parser.add_argument("--base-url", default=None, help="覆盖 config.base_url")
    parser.add_argument("--seed-code", default=None, help="种子用户 code（默认 config seed.login_code）")
    parser.add_argument("--pid", type=int, default=None, help="后端进程 PID（资源采样）")
    args = parser.parse_args()

    cfg = _load_config()
    base_url = args.base_url or os.environ.get("LOADTEST_BASE_URL", cfg["base_url"])
    tag = args.tag or _default_tag(args.mode)
    seed_code = args.seed_code or cfg["seed"]["login_code"]
    levels = [int(x) for x in (args.levels or ",".join(map(str, cfg["concurrency_levels"]))).split(",")]
    if args.probe:
        levels = levels + [lv for lv in cfg["probe_levels"] if lv not in levels]

    print(f"== 压测开始 mode={args.mode} tag={tag} levels={levels} base={base_url} ==")
    print(f"   限流说明: {cfg['rate_limit_note']}")

    # 健康 + 登录（种子用户 token 供 timeline/search/image）
    try:
        with httpx.Client(timeout=10) as c:
            r = c.get(f"{base_url}/healthz")
            r.raise_for_status()
        print("[load] 后端健康 ✅")
    except Exception as exc:
        print(f"[load] ❌ 后端不可达 {base_url}（{exc}）——请先启动 uvicorn（见 README）")
        return 1

    try:
        seed_token = _login_sync(base_url, seed_code, cfg["seed"]["device_id"])
    except Exception as exc:
        print(f"[load] ❌ 种子用户登录失败（{exc}）——请先运行 python seed_data.py")
        return 1
    seed_headers = {"Authorization": f"Bearer {seed_token}"}
    print(f"[load] 种子用户 {seed_code} 登录成功 ✅")

    sampler = ResourceSampler(args.pid, RESULTS / f"resource_{tag}.csv") if args.pid else None
    if sampler:
        sampler.start()

    summary_path = RESULTS / f"summary_{tag}.csv"
    csv_columns = [
        "path", "level", "mode", "tag", "samples", "p50_ms", "p90_ms", "p95_ms",
        "p99_ms", "avg_ms", "max_ms", "qps", "total", "ok", "errors",
        "rate_limited", "timeouts", "error_rate", "success_rate",
        "timeout_rate", "rate_limited_rate", "elapsed_s",
    ]
    new_file = not summary_path.exists()
    with open(summary_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if new_file:
            writer.writerow(csv_columns)

        async def _session():
            async with httpx.AsyncClient(base_url=base_url, timeout=cfg["request_timeout_ms"] / 1000) as client:
                for level in levels:
                    print(f"\n--- 并发档 {level} ---")
                    for path_name, path_cfg in cfg["paths"].items():
                        stats = await run_path(client, path_name, path_cfg, seed_headers, cfg, level, args.mode, tag)
                        writer.writerow([stats[k] for k in csv_columns])

        asyncio.run(_session())

    if sampler:
        sampler.stop()

    _plot_curves(summary_path, tag, args.mode)
    print(f"\n== 完成 tag={tag} 汇总 → {summary_path} ==")
    return 0


if __name__ == "__main__":
    sys.exit(main())
