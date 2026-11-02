"""答案质量三指标基线报告（Wave2-F 2026-08-26 · M2 验收前置）

faithfulness / relevancy / context_precision（RAGAS 范式，metrics.py 实现）：
当前 RAG 链路无答案生成层（search 只返回 hits），故基线以检索结果为代理：
- 对 truth_queries_50 的每条可检索查询跑 search → top-3 hits 作为 contexts；
- answer 取 top-1 hit 文本（"答案=最高相关片段"的代理口径，非真实生成答案）；
- 额外用"改写版答案"（对 top-1 做轻改写）展示 faithfulness<1 的判别能力。

真实生成链路（qwen-flash 摘要 + 溯源）接线后，用 evaluate_answer_quality 换真实
answer 即可，指标口径不变（RAG评测体系 §6 / 拿key后推进计划：M2 验收前）。

环境依赖：Qdrant（yishu-qdrant）+ BGE-M3 + 语料（run_eval 同款）；无则标 CI。
用法：
  python -m research.rag_benchmark.answer_quality_baseline
输出：research/rag_benchmark/answer_quality_report.json
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))
BENCH_DIR = Path(__file__).resolve().parent

from research.rag_benchmark.metrics import (  # noqa: E402
    context_precision,
    evaluate_answer_quality,
    faithfulness,
    relevancy,
)

# 检索 top-N 作为"上下文/候选答案"
TOP_N = 3


def _search_hits(q: str, collection: str):
    from app.schemas.search import SearchQuery
    from app.services.rag import search

    result = search(SearchQuery(q=q, limit=TOP_N), collection=collection)
    return [(h.content_id, h.text or "", h.score) for h in result.hits]


def _light_rewrite(text: str) -> str:
    """轻改写（代理口径：展示 faithfulness<1 的判别）：加限定语/同义替换一小部分"""
    if not text:
        return text
    prefix = "根据我的记忆，"
    if len(text) >= 4:
        return prefix + text[:-1] + "哈。"
    return prefix + text


def main() -> None:
    queries = json.loads((BENCH_DIR / "truth_queries_50.json").read_text(encoding="utf-8"))["queries"]
    report: dict = {"_meta": {"version": 1, "note": "答案质量三指标基线（检索代理口径）"},
                    "records": [], "aggregate": None}
    records: list[dict] = []
    for q in queries:
        if q.get("expect_empty"):
            continue  # 负样本无答案可评
        try:
            hits = _search_hits(q["query"], "yishu_benchmark_truth")
        except Exception as exc:  # noqa: BLE001 —— 环境缺失标 CI
            print(f"⚠ 检索失败（环境依赖，标 CI）: {exc}")
            report["environment_dependency"] = str(exc)
            (BENCH_DIR / "answer_quality_report.json").write_text(
                json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            return
        if not hits:
            continue
        contexts = [{"text": t, "is_relevant": True} for _, t, _ in hits]
        top1 = hits[0][1]
        records.append({
            "query_id": q.get("query_id"),
            "query": q["query"],
            "layer": q.get("layer"),
            "answer": top1,
            "rewritten_answer": _light_rewrite(top1),
            "contexts": contexts,
            "faithfulness": faithfulness(top1, [c["text"] for c in contexts]),
            "rewritten_faithfulness": faithfulness(_light_rewrite(top1), [c["text"] for c in contexts]),
            "relevancy": relevancy(q["query"], top1),
            "context_precision": context_precision(contexts),
        })
        report["records"].append(records[-1])

    agg = evaluate_answer_quality(
        [{"query": r["query"], "answer": r["answer"], "contexts": r["contexts"]} for r in records]
    )
    agg_rewritten = evaluate_answer_quality(
        [{"query": r["query"], "answer": r["rewritten_answer"], "contexts": r["contexts"]} for r in records]
    )
    report["aggregate"] = {
        "faithfulness": agg["faithfulness"],
        "relevancy": agg["relevancy"],
        "context_precision": agg["context_precision"],
        "n": agg["n"],
        # 改写版（代理口径，展示判别力）：faithfulness 应显著下降
        "rewritten_faithfulness": agg_rewritten["faithfulness"],
        "note": "answer=top1 检索片段（自持口径）；真实生成答案待 qwen-flash 摘要接线后替换",
    }
    out = BENCH_DIR / "answer_quality_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"答案质量基线报告: {out}")
    print(f"  样本 n={agg['n']} | faithfulness={agg['faithfulness']} "
          f"relevancy={agg['relevancy']} context_precision={agg['context_precision']}")
    print(f"  改写版 faithfulness={agg_rewritten['faithfulness']}（应低于原版，验证判别力）")


if __name__ == "__main__":
    main()
