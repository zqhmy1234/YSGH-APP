"""RRF 参数化（2026-11-19）：默认行为不变，权重/k/limit 可显式传入。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.services.vector_store import VectorStore


def _hit(pid: str, score: float = 1.0):
    return SimpleNamespace(
        id=f"point-{pid}",
        score=score,
        payload={"content_id": pid, "text": pid, "content_type": "text"},
    )


def test_default_weights_match_legacy_order():
    out = VectorStore._rrf_fuse([_hit("dense-1")], [_hit("sparse-1")])
    assert [x["content_id"] for x in out] == ["dense-1", "sparse-1"]
    assert out[0]["score"] == pytest.approx(0.7 / 61, abs=1e-4)
    assert out[1]["score"] == pytest.approx(0.3 / 61, abs=1e-4)


def test_custom_weights_change_order():
    out = VectorStore._rrf_fuse(
        [_hit("dense-1")], [_hit("sparse-1")],
        dense_weight=0.0, sparse_weight=1.0,
    )
    assert [x["content_id"] for x in out] == ["sparse-1", "dense-1"]
    assert out[0]["score"] == pytest.approx(1.0 / 61, abs=1e-4)


def test_limit_and_k_are_honored():
    hits = [_hit(f"d{i}") for i in range(5)]
    out = VectorStore._rrf_fuse(hits, [], limit=2, k=1)
    assert len(out) == 2
    assert out[0]["score"] == pytest.approx(0.7 / 2, abs=1e-4)
    assert out[1]["score"] == pytest.approx(0.7 / 3, abs=1e-4)


def test_content_type_propagates_from_sparse_when_dense_lacks_payload():
    dense = SimpleNamespace(id="point-d", score=1.0, payload=None)
    sparse = _hit("s")
    out = VectorStore._rrf_fuse([dense], [sparse])
    by_id = {x["content_id"]: x for x in out}
    assert by_id["s"]["content_type"] == "text"
