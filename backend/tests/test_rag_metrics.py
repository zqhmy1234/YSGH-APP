"""RAG 指标库单测（research/rag_benchmark/metrics.py）

手工构造已知排序验证数值正确性（recall/mrr/ndcg 手算一致）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "research"))

import pytest  # noqa: E402
from rag_benchmark.metrics import (  # noqa: E402
    evaluate_retrieval,
    hit_rate_at_k,
    mrr,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
)


def test_recall_at_k():
    assert recall_at_k({"a", "b"}, ["c", "a", "b"], 2) == 0.5
    assert recall_at_k({"a", "b"}, ["c", "d"], 2) == 0.0
    assert recall_at_k(set(), ["a"], 3) == 0.0


def test_hit_rate_at_k():
    assert hit_rate_at_k({"a"}, ["c", "a"], 2) == 1.0
    assert hit_rate_at_k({"a"}, ["c", "b"], 2) == 0.0
    assert hit_rate_at_k({"a"}, ["a", "b"], 1) == 1.0


def test_precision_at_k():
    assert precision_at_k({"a", "b"}, ["a", "c", "d"], 3) == pytest.approx(1 / 3)
    assert precision_at_k({"a", "b"}, ["a", "b"], 2) == 1.0


def test_mrr():
    assert mrr({"a"}, ["c", "a"]) == 0.5
    assert mrr({"a"}, ["a"]) == 1.0
    assert mrr({"a"}, ["c"]) == 0.0


def test_ndcg_at_k_hand_calc():
    # DCG = 1/log2(3) + 1/log2(4) ≈ 0.6309 + 0.5 = 1.1309
    # IDCG = 1 + 1/log2(3) ≈ 1.6309 → nDCG ≈ 0.6934
    assert round(ndcg_at_k({"a", "b"}, ["c", "a", "b"], 3), 4) == 0.6934


def test_evaluate_retrieval_aggregation():
    queries = [
        {"query": "q1", "expected": ["a", "b"]},
        {"query": "q2", "expected": ["x"]},
    ]
    ranker = lambda q: [("a", 0.9), ("b", 0.8), ("x", 0.7)]  # noqa: E731
    report = evaluate_retrieval(queries, ranker)
    assert report["n_queries"] == 2
    # q1 首中 rank1 → 1.0；q2 首中 rank3 → 1/3；均值 = 0.6667
    assert report["mrr"] == 0.6667
    assert report["hit_rate@3"] == 1.0
