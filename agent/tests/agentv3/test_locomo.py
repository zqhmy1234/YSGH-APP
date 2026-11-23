"""Real LoCoMo-10 benchmark: 10 conversations / 1986 gold-evidence questions."""

from __future__ import annotations

import json
from pathlib import Path

from harness.domain.locomo_eval import dialogue_turns, evaluate

DATA = Path(__file__).with_name("fixtures") / "locomo10.json"


def _load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def test_dataset_shape_and_turn_parser():
    data = _load()
    assert len(data) == 10
    turns = dialogue_turns(data[0]["conversation"])
    assert len(turns) > 100
    assert all(dia and text for dia, text in turns)
    assert len({dia for dia, _ in turns}) == len(turns)


def test_retrieval_quality_floor():
    metrics = evaluate(_load(), k=10)
    assert metrics["questions"] == 1982
    assert metrics["recall@10"] >= 0.52
    assert metrics["mrr"] >= 0.35
