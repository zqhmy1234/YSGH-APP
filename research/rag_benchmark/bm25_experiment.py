"""BM25 离线四组实验（E2，2026-11-19）

目的：在**不改生产主链**的前提下，回答「BM25 是否值得加」。
四组（同一查询集 / 语料 / 过滤）：
  A Dense
  B Dense + BGE-M3 Sparse（现行基线）
  C Dense + BM25
  D Dense + Sparse + BM25（三路等权 RRF 诊断）

数据：truth_queries_50（50 条）+ 合成 B/C/D 语料（131 条，与 truth-50 基线同口径）。
索引：独立 collection `yishu_benchmark_bm25`，不碰生产/其它基准库。
产物：research/rag_benchmark/bm25_experiment_report.json

用法（仓库根目录，backend 同一个 Python）：
    python -m research.rag_benchmark.bm25_experiment
    python -m research.rag_benchmark.bm25_experiment --no-gpu
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from pathlib import Path

import jieba

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "backend"))
BENCH = REPO / "research" / "rag_benchmark"
OUT = BENCH / "bm25_experiment_report.json"
COLLECTION = "yishu_benchmark_bm25"
DENSE_VEC = "text_vec"
SPARSE_VEC = "text_sparse"
LIMIT = 50
RRF_K = 60


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _tokenize(text: str) -> list[str]:
    toks = []
    for raw in jieba.lcut((text or "").lower()):
        tok = raw.strip()
        if tok and re.search(r"[\w一-鿿]", tok):
            toks.append(tok)
    return toks


class BM25:
    """极简 BM25（k1=1.5, b=0.75），只用于离线对照，不进生产。"""

    def __init__(self, docs: list[tuple[str, str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.ids = [d[0] for d in docs]
        self.doc_tokens = [_tokenize(d[1]) for d in docs]
        self.doc_len = [len(t) for t in self.doc_tokens]
        self.avgdl = (sum(self.doc_len) / len(self.doc_len)) if self.doc_len else 0.0
        self.tf: list[Counter] = [Counter(t) for t in self.doc_tokens]
        self.df: Counter = Counter()
        for toks in self.doc_tokens:
            self.df.update(set(toks))
        n = len(self.doc_tokens)
        self.idf = {
            t: math.log((n - df + 0.5) / (df + 0.5) + 1.0)
            for t, df in self.df.items()
        }

    def search(self, query: str, limit: int = LIMIT) -> list[tuple[str, float]]:
        q_tokens = _tokenize(query)
        scores: list[tuple[str, float]] = []
        for i, tf in enumerate(self.tf):
            score = 0.0
            dl = self.doc_len[i]
            for tok in q_tokens:
                f = tf.get(tok, 0)
                if not f:
                    continue
                denom = f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1.0))
                score += self.idf.get(tok, 0.0) * f * (self.k1 + 1) / denom
            if score > 0:
                scores.append((self.ids[i], score))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores[:limit]


def rrf(lists: list[list[tuple[str, float]]], weights: list[float],
        limit: int = LIMIT, k: int = RRF_K) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for hits, weight in zip(lists, weights, strict=True):
        for rank, (rid, _score) in enumerate(hits, 1):
            scores[rid] = scores.get(rid, 0.0) + weight / (k + rank)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    return ranked[:limit]


def _p95(values: list[float]) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return round(values[0], 2)
    return round(statistics.quantiles(values, n=20)[18], 2)


def _git(*args: str) -> str:
    try:
        git_bin = shutil.which("git") or "git"
        out = subprocess.run(  # noqa: S603 —— 固定 git 子命令，无用户输入拼接
            [git_bin, *args], cwd=REPO, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=False,
        )
        return out.stdout.strip()
    except Exception:  # noqa: BLE001
        return ""


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-gpu", action="store_true")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    if args.no_gpu:
        os.environ["EMBED_DEVICE"] = "cpu"
    else:
        os.environ.setdefault("EMBED_DEVICE", "cuda")

    from app.services.embedding import encode_dense, encode_sparse
    from app.services.vector_store import get_store

    from research.rag_benchmark.run_eval import (
        BENCH_DIR,
        _eval_truth_queries,
        _index,
        _resolve_truth_relevant,
    )

    items: list[dict] = []
    for fname in ("corpus_b_text.json", "corpus_c_voice.json", "corpus_d_filter.json"):
        items.extend(_load(BENCH_DIR / "corpora" / fname)["items"])
    queries = _load(BENCH_DIR / "truth_queries_50.json")["queries"]
    print(f"[bm25] 语料 {len(items)} 条 / 查询 {len(queries)} 条")

    corpus_labels: dict[str, set[str]] = {"__all__": {it["id"] for it in items}}
    for it in items:
        corpus_labels.setdefault(it.get("label") or it.get("content_type") or "text", set()).add(it["id"])
    modality_map = {it["id"]: it.get("content_type") or "text" for it in items}

    # 独立 collection，避免污染 truth-50 / A 批基线
    store = get_store()
    try:
        store.client.delete_collection(COLLECTION)
        print(f"[bm25] 已清空旧 collection {COLLECTION}")
    except Exception as exc:  # noqa: BLE001 —— 不存在即跳过
        print(f"[bm25] 清空旧 collection 跳过: {exc}")
    by_ct: dict[str, list[dict]] = {}
    for it in items:
        by_ct.setdefault(it.get("content_type") or "text", []).append(it)
    t0 = time.perf_counter()
    for ct, group in by_ct.items():
        _index(None, group, ct, collection=COLLECTION)
    index_ms = int((time.perf_counter() - t0) * 1000)
    print(f"[bm25] 索引完成 {index_ms} ms")

    bm25 = BM25([(it["id"], it["text"]) for it in items])
    print(f"[bm25] BM25 词表 {len(bm25.idf)} 项")

    # 预计算每条查询的 dense / sparse / bm25 排名（四组复用，避免重复编码）
    pre: dict[str, dict[str, list[tuple[str, float]]]] = {}
    dense_ms: list[float] = []
    sparse_ms: list[float] = []
    bm25_ms: list[float] = []
    for q in queries:
        text = q["query"]
        t = time.perf_counter()
        dense = encode_dense([text])[0]
        dense_ms.append((time.perf_counter() - t) * 1000)
        dense_hits = store.client.query_points(
            collection_name=COLLECTION, query=dense, using=DENSE_VEC, limit=LIMIT
        ).points
        dense_ranked = [
            (str(h.payload.get("content_id", h.id)), float(h.score))
            for h in dense_hits
        ]

        t = time.perf_counter()
        sparse = encode_sparse([text])[0]
        sparse_ms.append((time.perf_counter() - t) * 1000)
        if sparse:
            from qdrant_client import models as qm

            sparse_hits = store.client.query_points(
                collection_name=COLLECTION,
                query=qm.SparseVector(indices=list(sparse.keys()), values=list(sparse.values())),
                using=SPARSE_VEC, limit=LIMIT,
            ).points
            sparse_ranked = [
                (str(h.payload.get("content_id", h.id)), float(h.score))
                for h in sparse_hits
            ]
        else:
            sparse_ranked = []

        t = time.perf_counter()
        bm25_ranked = bm25.search(text, limit=LIMIT)
        bm25_ms.append((time.perf_counter() - t) * 1000)

        pre[text] = {"dense": dense_ranked, "sparse": sparse_ranked, "bm25": bm25_ranked}

    def ranker(group: str):
        def _rank(q: str):
            p = pre[q]
            if group == "A":
                return p["dense"]
            if group == "B":
                return rrf([p["dense"], p["sparse"]], [0.7, 0.3])
            if group == "C":
                return rrf([p["dense"], p["bm25"]], [0.7, 0.3])
            return rrf([p["dense"], p["sparse"], p["bm25"]], [1 / 3, 1 / 3, 1 / 3])

        return _rank

    groups: dict[str, dict] = {}
    for name in ("A", "B", "C", "D"):
        res = _eval_truth_queries(queries, ranker(name), corpus_labels, modality_map)
        groups[name] = {
            "retrieval": res["overall"].get("retrieval"),
            "negative_error_rate": res["overall"].get("negative_error_rate"),
            "negative_n": res["overall"].get("negative_n"),
            "placeholder_n": res["overall"].get("placeholder_n"),
            "unmatched_n": res["overall"].get("unmatched_n"),
            "cross_modal": res["overall"].get("cross_modal"),
        }
        print(
            f"[bm25] {name}: hit@3={((groups[name]['retrieval'] or {}).get('hit_rate@3'))} "
            f"neg={groups[name]['negative_error_rate']}"
        )

    # 三路权重小扫（诊断用；50 条查询上不据此定生产权重）
    sweeps = {
        "D_eq_1_1_1": (1 / 3, 1 / 3, 1 / 3),
        "D_6_2_2": (0.6, 0.2, 0.2),
        "D_7_15_15": (0.7, 0.15, 0.15),
        "D_5_25_25": (0.5, 0.25, 0.25),
        "D_8_1_1": (0.8, 0.1, 0.1),
    }
    weight_sweep: dict[str, dict] = {}
    for name, (dw, sw, bw) in sweeps.items():
        def _rank(q: str, dw=dw, sw=sw, bw=bw):
            p = pre[q]
            return rrf([p["dense"], p["sparse"], p["bm25"]], [dw, sw, bw])

        res = _eval_truth_queries(queries, _rank, corpus_labels, modality_map)
        weight_sweep[name] = {
            "weights": {"dense": dw, "sparse": sw, "bm25": bw},
            "hit_rate@3": (res["overall"].get("retrieval") or {}).get("hit_rate@3"),
            "recall@10": (res["overall"].get("retrieval") or {}).get("recall@10"),
            "mrr": (res["overall"].get("retrieval") or {}).get("mrr"),
        }

    # 新增召回率：BM25 top10 命中、而 B（Dense+Sparse）top10 未命中的相关条数
    new_vs_b = 0
    new_vs_a = 0
    relevant_total = 0
    details = []
    for q in queries:
        if q.get("expect_empty"):
            continue
        resolved = _resolve_truth_relevant(q, corpus_labels)
        if not resolved:
            continue
        relevant_total += len(resolved)
        text = q["query"]
        b_top = {rid for rid, _ in rrf([pre[text]["dense"], pre[text]["sparse"]], [0.7, 0.3], limit=10)}
        a_top = {rid for rid, _ in pre[text]["dense"][:10]}
        bm_top = {rid for rid, _ in pre[text]["bm25"][:10]}
        only_bm_vs_b = resolved & (bm_top - b_top)
        only_bm_vs_a = resolved & (bm_top - a_top)
        new_vs_b += len(only_bm_vs_b)
        new_vs_a += len(only_bm_vs_a)
        if only_bm_vs_b:
            details.append({"query_id": q.get("query_id"), "only_by_bm25_vs_b": sorted(only_bm_vs_b)})

    payload = {
        "_meta": {
            "name": "BM25 离线四组实验（E2）",
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "command": "python -m research.rag_benchmark.bm25_experiment",
            "git": {"branch": _git("rev-parse", "--abbrev-ref", "HEAD"),
                    "commit": _git("rev-parse", "HEAD"),
                    "dirty": bool(_git("status", "--porcelain"))},
            "env": {"EMBED_DEVICE": os.environ.get("EMBED_DEVICE")},
            "corpus": {"items": len(items), "queries": len(queries), "collection": COLLECTION},
            "index_ms": index_ms,
            "retrieval_p95_ms": {
                "dense_encode": _p95(dense_ms),
                "sparse_encode": _p95(sparse_ms),
                "bm25_search": _p95(bm25_ms),
            },
            "note": "BM25 只做离线对照；未改生产主链。D 组用三路等权 RRF（诊断基线）。",
            "caveat": (
                "本实验直连 Qdrant dense/sparse（不含 rag.search 的改写/类目/路由/PG 兜底），"
                "绝对 hit@3 与 truth-50 基线 0.9302 不可直接比较；四组之间同口径可比。"
                "负样本 1.0 是因为没有地板/过滤，不是模型结论。"
            ),
        },
        "groups": groups,
        "weight_sweep": weight_sweep,
        "new_recall": {
            "relevant_total": relevant_total,
            "bm25_only_vs_b": new_vs_b,
            "bm25_only_vs_a": new_vs_a,
            "queries_with_new_vs_b": details,
        },
    }
    out = Path(args.out) if args.out else OUT
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[bm25] 报告已输出: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
