"""corpus-A 文字搜图评估（RET-001 · B2-4）

queries_image.json（build_image_index.py 生成）→ 每查询走生产检索链路
（collection=yishu_benchmark + content_types=[image]）→ hit_rate@3 / latency。

用法：
  python scripts/eval_image_search.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

BENCH = Path(__file__).resolve().parent.parent / "research" / "rag_benchmark"


def main() -> int:
    from app.schemas.search import SearchQuery
    from app.services.rag import search

    queries = json.loads((BENCH / "queries" / "queries_image.json").read_text(encoding="utf-8"))["queries"]
    print(f"{len(queries)} 条文字搜图查询", flush=True)

    hit3 = 0
    latencies = []
    rows = []
    for q in queries:
        t0 = time.perf_counter()
        result = search(
            SearchQuery(q=q["query"], limit=5, content_types=["image"]),
            collection="yishu_benchmark",
        )
        ms = int((time.perf_counter() - t0) * 1000)
        latencies.append(ms)
        ranked = [h.content_id for h in result.hits]
        expected = set(q.get("expected", []))
        ok = bool(expected & set(ranked[:3]))
        hit3 += ok
        rows.append({
            "query": q["query"],
            "layer": q.get("layer"),
            "hit@3": ok,
            "ranked_top3": ranked[:3],
            "expected": sorted(expected)[:2],
            "ms": ms,
        })
        print(f"{'OK ' if ok else 'MISS'} [{q.get('layer','?')}] {q['query'][:30]} {ms}ms", flush=True)

    p95 = sorted(latencies)[max(0, int(len(latencies) * 0.95) - 1)] if latencies else 0
    report = {
        "corpus": "corpus-A (500 screenshots)",
        "n": len(queries),
        "hit_rate@3": round(hit3 / len(queries), 4) if queries else 0,
        "gate": "M1 文字搜图命中（RET-001）",
        "p95_ms": p95,
        "avg_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "rows": rows,
    }
    out = BENCH / "image_search_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"hit_rate@3={report['hit_rate@3']} p95={p95}ms → {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
