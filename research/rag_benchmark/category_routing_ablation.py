"""类目路由消融（hard / off / soft，2026-11-25）

同一 truth-50 查询/语料/召回深度，只改 `RAG_CLASS_ROUTING_MODE`：
  hard（现行）：content_class 硬过滤；
  off：不做类目过滤（召回上界诊断）；
  soft：不过滤，命中类目的候选 RRF 分 ×1.1（原型，仅对照）。
红线：user_id/软删/敏感/时间/地点仍是硬约束，只放松 content_class。

产物：research/rag_benchmark/category_routing_ablation_20261125.json
用法：python -m research.rag_benchmark.category_routing_ablation
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
BENCH = REPO / "research" / "rag_benchmark"
OUT = BENCH / "category_routing_ablation_20261125.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--modes", default="hard,off,soft")
    parser.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    modes = [x.strip() for x in args.modes.split(",") if x.strip()]

    env = dict(os.environ)
    if args.no_gpu:
        env["EMBED_DEVICE"] = "cpu"
    else:
        env.setdefault("EMBED_DEVICE", "cuda")
    env.setdefault("RAG_RECALL_LIMIT", "50")

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="class_route_") as td:
        for mode in modes:
            tmp = Path(td) / f"report_{mode}.json"
            run_env = dict(env)
            run_env["RAG_CLASS_ROUTING_MODE"] = mode
            cmd = [
                sys.executable, "-m", "research.rag_benchmark.run_eval",
                "--truth-50", "--out", str(tmp),
            ]
            print(f"[class] mode={mode} 运行...", flush=True)
            t0 = time.perf_counter()
            proc = subprocess.run(  # noqa: S603 —— cmd 固定，sys.executable 提供路径
                cmd, cwd=REPO, env=run_env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", check=False,
            )
            wall_ms = int((time.perf_counter() - t0) * 1000)
            if proc.stdout:
                print(proc.stdout, end="")
            if proc.stderr:
                print(proc.stderr, file=sys.stderr, end="")
            if not tmp.is_file():
                results[mode] = {"error": "run_eval 未产出报告", "wall_ms": wall_ms}
                continue
            r = json.loads(tmp.read_text(encoding="utf-8"))
            truth = r.get("truth") or {}
            overall = truth.get("overall") or {}
            layers = {}
            for layer, lr in (truth.get("layers") or {}).items():
                layers[layer] = {
                    "n": lr.get("n"),
                    "hit_rate@3": (lr.get("retrieval") or {}).get("hit_rate@3"),
                    "recall@10": (lr.get("retrieval") or {}).get("recall@10"),
                    "negative_fp": lr.get("negative_fp"),
                    "negative_n": lr.get("negative_n"),
                }
            results[mode] = {
                "wall_ms": wall_ms,
                "gates": truth.get("gates"),
                "overall": {
                    "retrieval": overall.get("retrieval"),
                    "negative_error_rate": overall.get("negative_error_rate"),
                    "negative_n": overall.get("negative_n"),
                },
                "layers": layers,
            }
            print(f"[class] {mode}: {results[mode]['overall'].get('retrieval')}")

    payload = {
        "_meta": {
            "name": "类目路由消融（truth-50：hard/off/soft）",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "command": "python -m research.rag_benchmark.category_routing_ablation",
            "modes": modes,
            "caveat": "soft 是 ×1.1 原型，不是标定后的软路由；绝对指标以 hard 为现行口径。",
        },
        "results": results,
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[class] 报告已输出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
