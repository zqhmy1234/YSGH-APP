"""RAG 检索指标库（行业标准,适配本项目）

检索层指标（BEIR/TREC 惯例）:
  recall_at_k / hit_rate_at_k / precision_at_k / mrr / ndcg_at_k
混合检索消融: dense-only / sparse-only / RRF 对比增益

用法:
  from research.rag_benchmark.metrics import recall_at_k, ndcg_at_k, evaluate_retrieval
"""
from __future__ import annotations

import math
from collections.abc import Iterable


def _truncate(ranked: list, k: int) -> list:
    return ranked[:k]


def recall_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """Recall@k：Top-k 中命中的相关数 / 总相关数（k=0 或无数相关返回 0）"""
    if k <= 0 or not relevant:
        return 0.0
    hit = sum(1 for rid in _truncate(ranked, k) if rid in relevant)
    return hit / len(relevant)


def hit_rate_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """HitRate@k：Top-k 是否至少命中一条相关（二元，业界常用）"""
    if k <= 0:
        return 0.0
    return 1.0 if any(rid in relevant for rid in _truncate(ranked, k)) else 0.0


def precision_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """Precision@k：Top-k 中相关占比（k=0 返回 0）"""
    if k <= 0:
        return 0.0
    hit = sum(1 for rid in _truncate(ranked, k) if rid in relevant)
    return hit / k


def mrr(relevant: set[str], ranked: list[str]) -> float:
    """MRR：首个相关结果的倒数排名（无命中返回 0）"""
    for i, rid in enumerate(ranked, 1):
        if rid in relevant:
            return 1.0 / i
    return 0.0


def _dcg(relevant: set[str], ranked: list[str], k: int) -> float:
    """DCG@k：相关度按 1/rank 衰减（二元相关）"""
    dcg = 0.0
    for i, rid in enumerate(_truncate(ranked, k), 1):
        if rid in relevant:
            dcg += 1.0 / math.log2(i + 1)
    return dcg


def ndcg_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """nDCG@k：DCG / IDCG（理想排序下最大 DCG；无相关返回 0）"""
    if k <= 0 or not relevant:
        return 0.0
    dcg = _dcg(relevant, ranked, k)
    ideal = sorted(relevant, key=lambda _x: 0)[:k]  # 理想排序 = 全部相关在前
    idcg = _dcg(relevant, ideal, k)
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_retrieval(
    queries: Iterable[dict],
    ranker,  # 函数：query → [(id, score), ...]
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> dict:
    """批量评估：对每个查询跑 ranker,聚合全部检索指标

    queries: [{query, expected: [id...], ...}]；ranker: callable(query_str) → [(id, score)]
    返回 {recall@k, hit_rate@k, precision@k, mrr, ndcg@k, n_queries}
    """
    agg = {f"recall@{k}": 0.0 for k in ks}
    agg.update({f"hit_rate@{k}": 0.0 for k in ks})
    agg.update({f"precision@{k}": 0.0 for k in ks})
    agg["mrr"] = 0.0
    agg["ndcg@3"] = 0.0
    n = 0
    for q in queries:
        ranked = [rid for rid, _score in ranker(q["query"])]
        relevant = set(q.get("expected", []))
        for k in ks:
            agg[f"recall@{k}"] += recall_at_k(relevant, ranked, k)
            agg[f"hit_rate@{k}"] += hit_rate_at_k(relevant, ranked, k)
            agg[f"precision@{k}"] += precision_at_k(relevant, ranked, k)
        agg["mrr"] += mrr(relevant, ranked)
        agg["ndcg@3"] += ndcg_at_k(relevant, ranked, 3)
        n += 1
    if n:
        for key in agg:
            agg[key] = round(agg[key] / n, 4)
    agg["n_queries"] = n
    return agg
