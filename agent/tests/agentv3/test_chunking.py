"""Chunking: sentence-aware, overlapping, offset-stable."""

from agentv3.memory.chunking import chunk_text


def _long(unit: str = "这一句话稍微长一点点。", n: int = 20) -> str:
    return unit * n


def test_empty_text_yields_no_chunks():
    assert chunk_text("   ", evidence_id="e1") == []


def test_chunks_cover_offsets_and_respect_budget():
    text = _long()
    chunks = chunk_text(text, evidence_id="e1", max_chars=120, overlap_chars=40)
    assert len(chunks) >= 2
    for chunk in chunks:
        assert chunk.end - chunk.start <= 120
        assert text[chunk.start : chunk.end].strip() == chunk.text
        assert chunk.evidence_id.startswith("e1#")
    assert chunks[0].start == 0 and chunks[-1].end == len(text)


def test_consecutive_chunks_overlap_by_one_tail_sentence():
    text = _long()
    chunks = chunk_text(text, evidence_id="e1", max_chars=120, overlap_chars=40)
    assert chunks[1].start <= chunks[0].end
    assert chunks[1].start > 0


def test_oversized_single_sentence_is_hard_split():
    text = "甲" * 300
    chunks = chunk_text(text, evidence_id="e2", max_chars=100)
    assert len(chunks) == 3
    assert all(c.end - c.start <= 100 for c in chunks)


def test_index_is_sequential():
    chunks = chunk_text(_long(), evidence_id="e3", max_chars=100)
    assert [c.index for c in chunks] == list(range(len(chunks)))
