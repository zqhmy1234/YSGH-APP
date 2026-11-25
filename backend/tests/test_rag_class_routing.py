"""类目路由模式（2026-11-25 实验）：默认 hard；off/soft 只放松 content_class。"""
from __future__ import annotations

from app.services.rag import _apply_class_soft_boost, _class_routing_mode


def test_class_routing_mode_default_is_hard(monkeypatch):
    monkeypatch.delenv("RAG_CLASS_ROUTING_MODE", raising=False)
    assert _class_routing_mode() == "hard"


def test_class_routing_mode_accepts_off_and_soft(monkeypatch):
    monkeypatch.setenv("RAG_CLASS_ROUTING_MODE", "off")
    assert _class_routing_mode() == "off"
    monkeypatch.setenv("RAG_CLASS_ROUTING_MODE", "soft")
    assert _class_routing_mode() == "soft"
    monkeypatch.setenv("RAG_CLASS_ROUTING_MODE", "bogus")
    assert _class_routing_mode() == "hard"


def test_soft_boost_reorders_matching_class():
    hits = [
        {"content_class": "todo", "score": 0.0100},
        {"content_class": "mixed", "score": 0.0098},
    ]
    out = _apply_class_soft_boost(hits, "todo")
    assert out[0]["content_class"] == "todo"
    assert out[0]["score"] == 0.011


def test_soft_boost_no_match_keeps_candidates():
    hits = [
        {"content_class": "todo", "score": 0.0100},
        {"content_class": "mixed", "score": 0.0098},
    ]
    out = _apply_class_soft_boost(hits, "quote")
    assert [h["content_class"] for h in out] == ["todo", "mixed"]
    assert out[0]["score"] == 0.0100
