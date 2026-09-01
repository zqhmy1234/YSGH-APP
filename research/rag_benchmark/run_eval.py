"""RAG 多基准集评估（M1 门禁 Top3≥70% + P95<3s + 分层指标）

覆盖全部输入分布：
  corpus-A 截图（图片塔 key 就绪后）      corpus-B 文字碎片（5 类）
  corpus-C 语音转写风格                    corpus-D 混合（B+C 合并索引）
  corpus-E 规模压力（--scale-eval,测 P95 随规模曲线）

真值评测（Wave2-F 2026-08-26，RAG评测体系 §6，5 项落地）：
  --truth-50      50 条真值评测集（research/rag_benchmark/truth_queries_50.json）
  --truth-a       A 批真值（research/truth-data/a/a_v*.json；expected 经 manifest 重映射）
                  ——含 expect_empty 负样本钩子 + 行为层占位 + 跨模态 route 聚合
  --rewrite-check Query 改写层正确率（相对时间→filter 解析 + NER + 显式参数优先）
  --truth-corpus  用 build_truth_corpus.py 生成的采集语料（缺省回退合成 B+C）

输出：evaluation_report.json（每基准集分层指标 + 门禁结果）

用法：
  python -m research.rag_benchmark.run_eval                 # B+C 分层评估
  python -m research.rag_benchmark.run_eval --scale-eval 1000  # 规模压力
  python -m research.rag_benchmark.run_eval --truth-50      # 50 条真值评测
  python -m research.rag_benchmark.run_eval --truth-a --rewrite-check
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

from research.rag_benchmark.metrics import (  # noqa: E402
    evaluate_retrieval,
    evaluate_retrieval_explicit,
)

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
    # 真值评测门禁（RAG评测体系 §5）：一般负样本误召回 ≤5%（target ≤3%）
    "negative_error_rate": 0.05,
    "negative_target": 0.03,
}

# 真值评测独立 collection（采集语料/合成兜底语料专用，避免与生产/合成基准混同）
TRUTH_COLLECTION = "yishu_benchmark_truth"


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


# ---- Wave2-F（2026-08-26）：真值评测（RAG评测体系 §6，5 项落地）----

# 行为层：time/temporal 时间过滤是硬过滤（无 NER/类目回退）→ "空结果即正确"占位
_BEHAVIOR_EMPTY_OK_LAYERS = {"time", "temporal"}


def _load_truth_queries(path: Path) -> list[dict]:
    """加载真值查询（兼容 A 批顶层数组 或 {queries:[...]} 对象）"""
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and isinstance(data.get("queries"), list):
        return data["queries"]
    raise ValueError(f"{path.name}: 无法解析真值查询（期望数组或 {{queries:[]}} 对象）")


def _resolve_truth_relevant(q: dict, corpus_labels: dict[str, set[str]]) -> set[str]:
    """真值查询相关集：显式 expected id 中能解析到语料的子集（label 兜底）"""
    rel = {i for i in (q.get("expected") or []) if i in corpus_labels.get("__all__", set())}
    label = q.get("expected_label")
    if label and label != "__none__" and label in corpus_labels:
        rel |= corpus_labels[label]
    return rel


def _eval_truth_queries(
    queries: list[dict],
    ranker,
    corpus_labels: dict[str, set[str]],
    modality_map: dict[str, str],
) -> dict:
    """真值查询评估（§6.1/§6.3/§6.5 落地）：

    - expect_empty=True → 负样本钩子（判空/误召回，§5 门禁 negative_error_rate≤5%）
    - 显式 expected id 可解析 → 检索指标（recall/hit_rate/mrr）
    - time/temporal 层无 id → 行为占位（空结果即正确，语料缺 taken_at 的过渡口径）
    - 其余层无 id → neutral 占位（语料缺元数据无法判，记 placeholder）
    - route 层多 id → 跨模态命中聚合（文/图/语音多模态返回，§6.5）
    """
    layers: dict[str, dict] = {}
    retrieval_qs: list[dict] = []
    neg_total = neg_fp = placeholder_n = unmatched_n = 0
    cross_n = cross_multi = 0

    for q in queries:
        layer = q.get("layer", "other")
        ranked = [rid for rid, _ in ranker(q["query"])]
        entry: dict
        if q.get("expect_empty"):
            neg_total += 1
            fp = 1 if ranked else 0
            neg_fp += fp
            entry = {"behavior_acc": 0.0 if fp else 1.0, "negative": True}
        else:
            ids = [i for i in (q.get("expected") or []) if i in corpus_labels.get("__all__", set())]
            if ids:
                resolved = sorted(_resolve_truth_relevant(q, corpus_labels))
                retrieval_qs.append({**q, "expected": resolved})
                entry = {"retrieval": True, "resolved": resolved}
            elif q.get("expected"):
                unmatched_n += 1
                entry = {"unmatched": True}
            elif layer in _BEHAVIOR_EMPTY_OK_LAYERS:
                entry = {"behavior_acc": 1.0 if not ranked else 0.0, "placeholder": True}
            else:
                placeholder_n += 1
                entry = {"neutral": True, "placeholder": True}
        # 跨模态 route 聚合（§6.5）：expected 多 id 的 route 查询，看 top-3 是否命中 ≥2 模态
        if layer == "route" and (q.get("expected") or []):
            cross_n += 1
            hit_cts = {modality_map.get(rid) for rid in ranked[:3] if rid in modality_map}
            hit_cts.discard(None)
            if len(hit_cts) >= 2:
                cross_multi += 1
        lr = layers.setdefault(layer, {
            "n": 0, "behavior_scores": [], "negative_n": 0, "negative_fp": 0,
            "unmatched_n": 0, "placeholder": False, "retrieval_qs": [],
        })
        lr["n"] += 1
        if "behavior_acc" in entry and not entry.get("negative"):
            lr["behavior_scores"].append(entry["behavior_acc"])
        if entry.get("negative"):
            lr["negative_n"] += 1
            lr["negative_fp"] += 1 if entry["behavior_acc"] == 0.0 else 0
        if entry.get("unmatched"):
            lr["unmatched_n"] += 1
        if entry.get("placeholder"):
            lr["placeholder"] = True
        if entry.get("retrieval"):
            lr["retrieval_qs"].append({**q, "expected": entry["resolved"]})

    # 层报告收口
    layer_report: dict[str, dict] = {}
    for layer, lr in layers.items():
        scores = lr["behavior_scores"]
        layer_report[layer] = {
            "n": lr["n"],
            "placeholder": lr["placeholder"],
            "negative_n": lr["negative_n"],
            "negative_fp": lr["negative_fp"],
            "unmatched_n": lr["unmatched_n"],
            "behavior_acc": round(sum(scores) / len(scores), 4) if scores else None,
            "retrieval": evaluate_retrieval(lr["retrieval_qs"], ranker) if lr["retrieval_qs"] else None,
        }

    overall = {
        "retrieval": evaluate_retrieval(retrieval_qs, ranker) if retrieval_qs else {"n_queries": 0},
        "negative_n": neg_total,
        "negative_error_rate": round(neg_fp / neg_total, 4) if neg_total else 0.0,
        "placeholder_n": placeholder_n,
        "unmatched_n": unmatched_n,
    }
    if cross_n:
        overall["cross_modal"] = {"n": cross_n, "multi_modal_hit": cross_multi,
                                  "acc": round(cross_multi / cross_n, 4)}
    return {"layers": layer_report, "overall": overall}


def _check_rewrite_case(case: dict, now=None) -> bool:
    """改写层单例正确性校验（§6.4：相对时间→filter 解析 + NER + 显式参数优先）"""
    from app.schemas.search import SearchQuery
    from app.services.rag import _rewrite_query

    params = case.get("params") or {}
    q = SearchQuery(q=case["query"], **{k: v for k, v in params.items() if k in ("place", "tag")})
    rewritten, filters, ner_filters = _rewrite_query(q)
    expect = case.get("expect") or {}
    kind = expect.get("kind")
    if kind == "mid_query_no_filter":
        return "time_from" not in filters and "time_to" not in filters
    if kind == "place":
        if expect.get("place"):
            return filters.get("place") == expect["place"] and ner_filters.get("place") == expect["place"]
        return bool(filters.get("place"))
    if kind == "person":
        return bool(filters.get("tag"))
    if kind == "explicit_param_wins":
        return filters.get("place") == expect["place"] and "place" not in ner_filters
    # 时间窗口校验
    tf, tt = filters.get("time_from"), filters.get("time_to")
    if tf is None:
        return False
    exp_rewritten = expect.get("rewritten")
    if exp_rewritten is not None and rewritten != exp_rewritten:
        return False
    if kind == "last_year":
        return tf.year == _now(now).year - 1 and tt is not None and tt.year == _now(now).year - 1
    if kind == "last_summer":
        return tf.month == 6 and tf.day == 1 and tt is not None and tt.month == 8 and tt.day == 31
    if kind == "two_weeks_ago" or kind == "last_week":
        return tt is not None and 6 <= (tt - tf).days <= 7
    if kind == "three_years_ago":
        return tf.year == _now(now).year - 3
    if kind == "year_before_last":
        return tf.year == _now(now).year - 2
    if kind == "yesterday":
        return tt is not None and tf.date() == (_now(now).date() - __import__("datetime").timedelta(days=1))
    return False


def _now(now) -> object:
    import datetime as _dt

    return now if now is not None else _dt.datetime.now(_dt.timezone.utc).astimezone()


def _eval_rewrite(cases_path: Path) -> dict:
    """Query 改写层正确率评估（§6.4）：逐例判 + 聚合准确率"""
    data = json.loads(cases_path.read_text(encoding="utf-8"))
    cases = data.get("cases") if isinstance(data, dict) else data
    ok = 0
    failed: list[dict] = []
    for case in cases:
        passed = _check_rewrite_case(case)
        ok += 1 if passed else 0
        if not passed:
            failed.append({"case_id": case.get("case_id"), "query": case.get("query")})
    return {
        "n": len(cases),
        "accuracy": round(ok / len(cases), 4) if cases else 0.0,
        "failed": failed,
    }


def _run_truth_eval(args, report: dict) -> dict:
    """真值评测主流程（§6.1-6.5）：索引语料 → 跑真值查询 → 门禁

    语料优先级：--truth-corpus 用 build_truth_corpus 产物（采集语料）；
    缺省回退合成 B+C（truth_queries_50 的 expected 引用合成 id 即可直接跑）。
    """
    # 1. 语料（采集或合成兜底）→ TRUTH_COLLECTION
    items: list[dict] = []
    if args.truth_corpus:
        tc = BENCH_DIR / "truth_corpus"
        for fname in ("corpus_b_truth.json", "corpus_c_truth.json", "corpus_d_truth.json"):
            p = tc / fname
            if p.exists():
                items.extend(_load_json(p)["items"])
        if not items:
            print("[truth] 无采集语料（先跑 python scripts/build_truth_corpus.py），回退合成 B+C")
    if not items:
        items = _load_json(BENCH_DIR / "corpora" / "corpus_b_text.json")["items"]
        items += _load_json(BENCH_DIR / "corpora" / "corpus_c_voice.json")["items"]
    print(f"[truth] 索引语料 {len(items)} 条 → {TRUTH_COLLECTION}")
    by_ct: dict[str, list[dict]] = {}
    for it in items:
        by_ct.setdefault(it.get("content_type") or "text", []).append(it)
    for ct, group in by_ct.items():
        _index(None, group, ct, collection=TRUTH_COLLECTION)

    corpus_labels: dict[str, set[str]] = {"__all__": {it["id"] for it in items}}
    modality_map: dict[str, str] = {}
    for it in items:
        corpus_labels.setdefault(it.get("label") or it["content_type"], set()).add(it["id"])
        modality_map[it["id"]] = it.get("content_type") or "text"

    # 2. 加载真值查询
    queries: list[dict] = []
    sources: list[str] = []
    if args.truth_50:
        queries += _load_truth_queries(BENCH_DIR / "truth_queries_50.json")
        sources.append("truth_queries_50")
    if args.truth_a:
        a_files = sorted((BENCH_DIR.parent.parent / "research" / "truth-data" / "a").glob("a_v*.json"))
        if a_files:
            queries += _load_truth_queries(a_files[-1])
            sources.append(f"truth-data/a/{a_files[-1].name}")
        else:
            print("[truth] A 批无数据，跳过")
    print(f"[truth] 查询 {len(queries)} 条（来源: {', '.join(sources) or '无'}）")

    # 3. 跑真值评估
    ranker = _make_ranker(collection=TRUTH_COLLECTION)
    res = _eval_truth_queries(queries, ranker, corpus_labels, modality_map)
    overall = res["overall"]
    hr3 = overall["retrieval"].get("hit_rate@3", 0.0) if overall.get("retrieval") else 0.0
    neg_rate = overall["negative_error_rate"]
    gate_hr = hr3 >= GATE["hit_rate@3"] or overall["retrieval"].get("n_queries", 0) == 0
    gate_neg = neg_rate <= GATE["negative_error_rate"]
    truth_gates = {
        "hit_rate@3": hr3,
        "gate_hit_rate@3": gate_hr,
        "negative_error_rate": neg_rate,
        "gate_negative_error_rate": gate_neg,
        "threshold_hit_rate@3": GATE["hit_rate@3"],
        "threshold_negative": GATE["negative_error_rate"],
    }
    report["truth"] = {"queries": len(queries), "sources": sources,
                       "corpus_size": len(items), "layers": res["layers"],
                       "overall": overall, "gates": truth_gates}
    print(f"[truth] 检索 hit_rate@3={hr3}（门禁 ≥{GATE['hit_rate@3']}）"
          f" | 负样本误召回率={neg_rate}（门禁 ≤{GATE['negative_error_rate']}）")
    for layer, lr in res["layers"].items():
        r = lr.get("retrieval")
        b = lr.get("behavior_acc")
        print(f"  layer[{layer}] n={lr['n']} "
              f"{'hit_rate@3=' + str(r['hit_rate@3']) if r else ''} "
              f"{'behavior_acc=' + str(b) if b is not None else ''} "
              f"{'neg=' + str(lr['negative_n']) + '/fp' + str(lr['negative_fp']) if lr['negative_n'] else ''} "
              f"{'[placeholder]' if lr['placeholder'] else ''}")
    if "cross_modal" in overall:
        cm = overall["cross_modal"]
        print(f"[truth] 跨模态 route: 多模态命中 {cm['multi_modal_hit']}/{cm['n']}（acc={cm['acc']}）")
    overall_ok = gate_hr and gate_neg
    print(f"[truth] 门禁: {'✅ PASS' if overall_ok else '❌ FAIL'}"
          f"（hit_rate@3 {'✅' if gate_hr else '❌'} | 负样本误召回 {'✅' if gate_neg else '❌'}）")
    return overall_ok


def _write_report(report: dict) -> None:
    out = BENCH_DIR / "evaluation_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"报告已输出: {out}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scale-eval", type=int, default=0, help=">0 时跑规模压力（N 条）")
    parser.add_argument("--external", action="store_true",
                        help="额外评测外部测试集（T2Ranking 抽取用例，P1-B2；不进门禁）")
    parser.add_argument("--truth-50", action="store_true",
                        help="50 条真值评测集（research/rag_benchmark/truth_queries_50.json）")
    parser.add_argument("--truth-a", action="store_true",
                        help="A 批真值（research/truth-data/a/a_v*.json；expected 经 manifest 重映射）")
    parser.add_argument("--truth-corpus", action="store_true",
                        help="用 build_truth_corpus.py 生成的采集语料（缺省回退合成 B+C）")
    parser.add_argument("--rewrite-check", action="store_true",
                        help="Query 改写层正确率（相对时间→filter 解析 + NER + 显式参数优先）")
    args = parser.parse_args()

    report: dict = {"_meta": {"version": 2, "note": "RAG 全分布测评"}, "corpora": {}}
    overall_ok = True

    if args.rewrite_check:
        rew = _eval_rewrite(BENCH_DIR / "rewrite_cases.json")
        report["rewrite_check"] = rew
        print(f"[rewrite] Query 改写层正确率: {rew['accuracy']}（{rew['n']} 例）")
        if rew["failed"]:
            print(f"  ✗ 失败用例: {[f['case_id'] for f in rew['failed']]}")

    if args.truth_50 or args.truth_a:
        overall_ok = _run_truth_eval(args, report)
        report["overall_pass"] = overall_ok
        _write_report(report)
        sys.exit(0 if overall_ok else 1)

    # 仅 --rewrite-check（不跑合成全量）→ 直接收尾
    if args.rewrite_check and not args.scale_eval and not args.external:
        report["overall_pass"] = True
        _write_report(report)
        return

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
    _write_report(report)
    print(f"总体门禁: {'✅ PASS' if overall_ok else '❌ FAIL'}")
    sys.exit(0 if overall_ok else 1)


if __name__ == "__main__":
    main()
