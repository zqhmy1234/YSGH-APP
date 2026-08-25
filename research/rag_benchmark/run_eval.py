"""RAG 多基准集评估（M1 门禁 Top3≥70% + P95<3s + 分层指标）

覆盖全部输入分布：
  corpus-A 截图（图片塔 key 就绪后）      corpus-B 文字碎片（5 类）
  corpus-C 语音转写风格                    corpus-D 混合（B+C 合并索引）
  corpus-E 规模压力（--scale-eval,测 P95 随规模曲线）

输出：evaluation_report.json（每基准集分层指标 + 门禁结果）

用法：
  python -m research.rag_benchmark.run_eval                 # B+C 分层评估
  python -m research.rag_benchmark.run_eval --scale-eval 1000  # 规模压力
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "backend"))

BENCH_DIR = Path(__file__).resolve().parent

from research.rag_benchmark.metrics import evaluate_retrieval, evaluate_retrieval_explicit  # noqa: E402

# 修复（审查 MAJOR）：基准语料索引进生产 collection yishu_contents 会污染真实
# 检索空间（payload 带 benchmark 标记但搜索不排除）。改用独立 collection
# yishu_benchmark——基准索引与评估全量隔离，生产检索零影响。
BENCH_COLLECTION = "yishu_benchmark"
# 文字语料评测用独立 collection（2026-08-20：corpus-A 图片 498 点已入 yishu_benchmark，
# 混同会让 text/route 查询命中图片点 → hit_rate/route_acc 虚低；文字评测隔离）
TEXT_BENCH_COLLECTION = "yishu_benchmark_text"
# 外部评测集独立 collection（P1-B2 2026-08-25：T2Ranking 抽取用例，与合成库隔离）
EXT_COLLECTION = "yishu_benchmark_ext"

GATE = {
    "hit_rate@3": 0.70,     # M1 门禁（Top3≥70%，与产品口径一致）
    "p95_ms": 3000,         # M1 门禁
    "route_acc": 0.90,      # 路由行为准确率
    "temporal_acc": 0.90,   # 时间过滤行为准确率
}


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _index(db, items: list[dict], content_type: str, collection: str | None = None) -> None:
    """语料 → Qdrant 基准 collection（幂等：同 id 覆盖）

    collection=None → 默认文字基准库 TEXT_BENCH_COLLECTION；
    外部评测集（--external）传 EXT_COLLECTION 隔离。
    """
    from app.services.embedding import encode_dense, encode_sparse
    from app.services.vector_store import get_store

    col = collection or TEXT_BENCH_COLLECTION
    store = get_store()
    store.ensure_collection(col)
    texts = [it["text"] for it in items]
    denses = encode_dense(texts)
    sparses = encode_sparse(texts)
    for it, dense, sparse in zip(items, denses, sparses, strict=True):
        store.upsert_content(
            content_id=it["id"],
            text=it["text"],
            dense=dense,
            sparse=sparse,
            payload={
                "content_type": content_type,
                "label": it.get("label"),
                # P1-A（2026-08-25）：类目路由按 content_class 过滤（与生产 payload
                # 字段一致；label 保留兼容旧逻辑）
                "content_class": it.get("label"),
                "benchmark": "rag-distribution",
                "text": it["text"],
            },
            collection=col,
        )


def _make_ranker(content_type: str | None = None, collection: str | None = None):
    """构造 ranker：query → [(id, score)]（可按类型过滤测路由/过滤）"""
    from app.schemas.search import SearchQuery
    from app.services.rag import search

    col = collection or TEXT_BENCH_COLLECTION

    def ranker(q: str):
        req = SearchQuery(q=q, limit=10)
        if content_type:
            req.content_types = [content_type]
        result = search(req, collection=col)
        return [(h.content_id, h.score) for h in result.hits]

    return ranker


def _resolve_relevant(q: dict, corpus_labels: dict[str, set[str]]) -> set[str]:
    """解析相关集：显式 expected id ∪ expected_label 对应的全部 id（label 级相关性）

    expected_label="__none__" 表示期望无相关（验证不误召回）。
    """
    rel = set(q.get("expected", []) or [])
    label = q.get("expected_label")
    if label and label != "__none__" and label in corpus_labels:
        rel |= corpus_labels[label]
    return rel


def _eval_corpus(queries: list[dict], ranker, corpus_labels: dict[str, set[str]]) -> tuple[dict, dict]:
    """跑分层查询,返回 (分层指标, 门禁检查)

    temporal/route 层用行为判定（返回空/类型正确），不进 recall 聚合。
    """
    layers: dict[str, list[dict]] = {}
    for q in queries:
        layers.setdefault(q.get("layer", "other"), []).append(q)

    report: dict[str, dict] = {}
    gates: dict[str, bool] = {}
    for layer, qs in layers.items():
        if layer in ("temporal", "route"):
            ok = 0
            for q in qs:
                ranked = [rid for rid, _ in ranker(q["query"])]
                if layer == "temporal":
                    ok += 1 if not ranked else 0
                else:
                    rel = _resolve_relevant(q, corpus_labels)
                    ok += 1 if (rel and any(r in rel for r in ranked[:3])) or (not rel and not ranked) else 0
            acc = round(ok / len(qs), 4)
            report[layer] = {"behavior_acc": acc, "n": len(qs)}
            gates[f"{layer}_acc"] = acc >= GATE.get("route_acc" if layer == "route" else "temporal_acc", 0.9)
        else:
            eval_qs = [{**q, "expected": sorted(_resolve_relevant(q, corpus_labels))} for q in qs]
            metric = evaluate_retrieval(eval_qs, ranker)
            # P0-B（2026-08-25）：显式相关口径诊断——相关集 = 显式 expected id，
            # 不被 label 全集稀释；只统计带显式 id 的查询（n_queries 为其数量）。
            metric["explicit"] = evaluate_retrieval_explicit(qs, ranker)
            report[layer] = metric
    return report, gates


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale-eval", type=int, default=0, help=">0 时跑规模压力（N 条）")
    parser.add_argument("--external", action="store_true",
                        help="额外评测外部测试集（T2Ranking 抽取用例，P1-B2；不进门禁）")
    args = parser.parse_args()

    report: dict = {"_meta": {"version": 2, "note": "RAG 全分布测评"}, "corpora": {}}
    overall_ok = True

    if args.scale_eval > 0:
        # corpus-E 规模压力：只测延迟随规模（--scale-eval N 条）
        from app.schemas.search import SearchQuery
        from app.services.rag import search

        corpus = _load_json(BENCH_DIR / "corpora" / "corpus_e_scale.json")["items"][: args.scale_eval]
        print(f"[scale] 索引 {len(corpus)} 条合成文本...")
        t0 = time.perf_counter()
        _index(None, corpus, "text")
        idx_ms = int((time.perf_counter() - t0) * 1000)
        latencies = []
        for i in range(20):
            t0 = time.perf_counter()
            search(SearchQuery(q=f"工作备忘 {i * 100}", limit=5), collection=BENCH_COLLECTION)
            latencies.append((time.perf_counter() - t0) * 1000)
        p95 = sorted(latencies)[int(len(latencies) * 0.95) - 1]
        report["corpora"]["scale"] = {"size": len(corpus), "index_ms": idx_ms, "p95_ms": round(p95, 1)}
        report["corpora"]["scale"]["gate_p95"] = p95 < GATE["p95_ms"]
        overall_ok &= p95 < GATE["p95_ms"]
        print(f"[scale] {len(corpus)} 条索引 {idx_ms}ms | 查询 P95 {p95:.0f}ms（门禁 <{GATE['p95_ms']}ms）")
    else:
        # corpus-B + corpus-C 文本/语音分布（corpus-D 混合 = B+C 同索引）
        b_items = _load_json(BENCH_DIR / "corpora" / "corpus_b_text.json")["items"]
        c_items = _load_json(BENCH_DIR / "corpora" / "corpus_c_voice.json")["items"]
        queries = _load_json(BENCH_DIR / "queries" / "queries_text.json")["queries"]

        print(f"[B+C] 索引 {len(b_items) + len(c_items)} 条（混合库 corpus-D）...")
        t0 = time.perf_counter()
        _index(None, b_items, "text")
        _index(None, c_items, "voice")
        idx_ms = int((time.perf_counter() - t0) * 1000)

        corpus_labels: dict[str, set[str]] = {"__all__": {it["id"] for it in b_items + c_items}}
        for it in b_items:
            corpus_labels.setdefault(it["label"], set()).add(it["id"])
        for it in c_items:
            corpus_labels.setdefault(it["label"], set()).add(it["id"])

        ranker = _make_ranker()
        corpora_report, gates = _eval_corpus(queries, ranker, corpus_labels)
        report["corpora"]["mixed"] = {
            "size": len(b_items) + len(c_items),
            "index_ms": idx_ms,
            "layers": corpora_report,
            "gates": gates,
        }

        # 门禁：检索层整体 hit_rate@3（M1 主线门禁：Top3≥70%）+ 行为层准确率
        retrieval_qs = [
            {**q, "expected": sorted(_resolve_relevant(q, corpus_labels))}
            for q in queries if q.get("layer") not in ("temporal", "route")
        ]
        all_metrics = evaluate_retrieval(retrieval_qs, ranker)
        # P0-B：显式口径诊断（用原始查询的显式 id，避免 label 全集误当显式相关）
        retrieval_raw = [q for q in queries if q.get("layer") not in ("temporal", "route")]
        all_metrics["explicit"] = evaluate_retrieval_explicit(retrieval_raw, ranker)
        report["corpora"]["mixed"]["overall"] = all_metrics
        gate_hit = all_metrics["hit_rate@3"] >= GATE["hit_rate@3"]
        overall_ok &= gate_hit and all(gates.values())
        print(f"[B+C] 检索层整体 hit_rate@3={all_metrics['hit_rate@3']}（门禁 ≥{GATE['hit_rate@3']}）"
              f" recall@3={all_metrics['recall@3']} mrr={all_metrics['mrr']} ndcg@3={all_metrics['ndcg@3']}")
        ex = all_metrics["explicit"]
        print(f"[B+C] 显式口径诊断（n={ex['n_queries']}）: recall@3={ex.get('recall@3')} "
              f"hit_rate@3={ex.get('hit_rate@3')} mrr={ex.get('mrr')}")
        for layer, m in corpora_report.items():
            print(f"  layer[{layer}] {m}")

        # P1-B2（2026-08-25）：外部测试集（T2Ranking 抽取，见 build_external_corpus.py）
        # 独立 collection 隔离评测；所有查询带显式 expected id → recall 即显式口径；
        # 仅诊断/回归哨兵，不进 M1 门禁。
        if args.external:
            ext_items = _load_json(BENCH_DIR / "corpora" / "corpus_ext_t2r.json")["items"]
            ext_queries = _load_json(BENCH_DIR / "queries" / "queries_ext.json")["queries"]
            print(f"[EXT] 索引外部语料 {len(ext_items)} 条 → {EXT_COLLECTION} ...")
            t0 = time.perf_counter()
            _index(None, ext_items, "text", collection=EXT_COLLECTION)
            ext_ms = int((time.perf_counter() - t0) * 1000)
            ext_ranker = _make_ranker(collection=EXT_COLLECTION)
            ext_metrics = evaluate_retrieval(ext_queries, ext_ranker)
            ext_metrics["explicit"] = evaluate_retrieval_explicit(ext_queries, ext_ranker)
            report["corpora"]["external"] = {
                "size": len(ext_items),
                "n_queries": len(ext_queries),
                "index_ms": ext_ms,
                "metrics": ext_metrics,
                "gate": False,
            }
            print(f"[EXT] {len(ext_queries)} 查询: hit_rate@3={ext_metrics['hit_rate@3']} "
                  f"recall@3={ext_metrics['recall@3']} mrr={ext_metrics['mrr']} "
                  f"precision@3={ext_metrics['precision@3']} ndcg@3={ext_metrics['ndcg@3']}")

    report["overall_pass"] = overall_ok
    out = BENCH_DIR / "evaluation_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已输出: {out}")
    print(f"总体门禁: {'✅ PASS' if overall_ok else '❌ FAIL'}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
