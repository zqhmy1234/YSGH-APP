"""RAG 精排 prompt 元数据 + CrossEncoder 显式 device（2026-11-17）。

覆盖：
- `_build_prompt` 把 SearchHit 的 taken_at/place/content_type/event_title 写进候选；
- 无 SearchHit 的旧调用方保持原格式；
- `services.rerank._load_model` 在 GPU 可用时把 device="cuda" 传给 CrossEncoder。
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.schemas.search import SearchHit
from app.services import rerank as rerank_mod
from app.services.llm_ops.rerank import _build_prompt


def _hit(**overrides) -> SearchHit:
    data = {
        "content_id": "c1",
        "content_type": "photo",
        "text": "外滩夜景",
        "taken_at": datetime(2025, 7, 18, 20, 30, tzinfo=UTC),
        "place": "上海",
        "event_id": "e1",
        "event_title": "暑假出游",
        "score": 0.9,
        "trace": {},
    }
    data.update(overrides)
    return SearchHit(**data)


def test_build_prompt_includes_hit_metadata():
    prompt = _build_prompt(
        "上个月在外滩拍的照片",
        [{"id": "c1", "text": "外滩夜景", "score": 0.9, "hit": _hit()}],
    )
    assert "[0]" in prompt
    assert "类型：photo" in prompt
    assert "时间：2025-07-18" in prompt
    assert "地点：上海" in prompt
    assert "事件：暑假出游" in prompt
    assert "正文：外滩夜景" in prompt


def test_build_prompt_keeps_legacy_format_without_hit():
    prompt = _build_prompt("杭州旅行", [{"id": "c1", "text": "西湖", "score": 0.9}])
    assert "[0] 西湖" in prompt
    assert "正文：" not in prompt


def test_load_model_passes_explicit_cuda_device(monkeypatch, tmp_path):
    model_dir = tmp_path / "bge-reranker-v2-m3"
    model_dir.mkdir()
    (model_dir / "config.json").write_text("{}", encoding="utf-8")

    captured: dict = {}

    class FakeCrossEncoder:
        def __init__(self, *args, **kwargs):
            captured.update(kwargs)

    import sentence_transformers

    monkeypatch.setattr(rerank_mod.settings, "reranker_model", str(model_dir))
    monkeypatch.setattr(rerank_mod, "_gpu_available", lambda: True)
    monkeypatch.setattr(sentence_transformers, "CrossEncoder", FakeCrossEncoder)
    rerank_mod._load_model.cache_clear()
    try:
        model = rerank_mod._load_model()
    finally:
        rerank_mod._load_model.cache_clear()

    assert model is not None
    assert captured.get("device") == "cuda"
