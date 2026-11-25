"""召回深度消融（E3 第一半：20 / 50 / 100）

同一 truth-50 查询/语料/开关，只改 `RAG_RECALL_LIMIT`（rag.search 的召回窗口），
看正确结果是否进入候选集、以及指标/耗时变化。
产物：research/rag_benchmark/recall_depth_ablation_20261125.json

用法（仓库根目录）：
    python -m research.rag_benchmark.recall_depth_ablation
    python -m research.rag_benchmark.recall_depth_ablation --limits 20,50,100 --no-gpu
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
OUT = BENCH / "recall_depth_ablation_20261125.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limits", default="20,50,100")
    parser.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()
    limits = [int(x) for x in args.limits.split(",") if x.strip()]

    env = dict(os.environ)
    if args.no_gpu:
        env["EMBED_DEVICE"] = "cpu"
    else:
        env.setdefault("EMBED_DEVICE", "cuda")

    results: dict[str, dict] = {}
    with tempfile.TemporaryDirectory(prefix="depth_") as td:
        for limit in limits:
            tmp = Path(td) / f"report_{limit}.json"
            run_env = dict(env)
            run_env["RAG_RECALL_LIMIT"] = str(limit)
            cmd = [
                sys.executable, "-m", "research.rag_benchmark.run_eval",
                "--truth-50", "--out", str(tmp),
            ]
            print(f"[depth] RAG_RECALL_LIMIT={limit} 运行...", flush=True)
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
                results[str(limit)] = {"error": "run_eval 未产出报告", "wall_ms": wall_ms}
                continue
            r = json.loads(tmp.read_text(encoding="utf-8"))
            truth = r.get("truth") or {}
            overall = truth.get("overall") or {}
            results[str(limit)] = {
                "wall_ms": wall_ms,
                "per_query_ms": round(wall_ms / max(1, truth.get("queries") or 1), 2),
                "gates": truth.get("gates"),
                "overall": {
                    "retrieval": overall.get("retrieval"),
                    "negative_error_rate": overall.get("negative_error_rate"),
                    "negative_n": overall.get("negative_n"),
                    "placeholder_n": overall.get("placeholder_n"),
                    "unmatched_n": overall.get("unmatched_n"),
                },
                "settings": truth.get("settings"),
            }
            print(f"[depth] {limit}: {results[str(limit)]['overall'].get('retrieval')}")

    payload = {
        "_meta": {
            "name": "召回深度消融（truth-50，E3 第一半）",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "command": "python -m research.rag_benchmark.recall_depth_ablation",
            "limits": limits,
            "caveat": (
                "只改 rag.search 的 RAG_RECALL_LIMIT；语料/查询/开关/时间锚点不变。"
                "per_query_ms 是进程墙钟/查询数，不是 P95。"
            ),
        },
        "results": results,
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[depth] 报告已输出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
