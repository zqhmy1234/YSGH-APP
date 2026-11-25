"""Chunking parity: sentence-aware windows with overlap, stable offsets.

Linger's retrieval chunks long documents before embedding; the contract we keep is
  * offsets refer to the original text (so an evidence id can point at a chunk),
  * a chunk never exceeds the embedding budget,
  * consecutive chunks overlap by at most one tail sentence.
"""

from __future__ import annotations

from harness.contracts import TextChunk

SENTENCE_END = "。！？!?；;\n"


def chunk_text(
    text: str,
    *,
    evidence_id: str,
    max_chars: int = 500,
    overlap_chars: int = 80,
) -> list[TextChunk]:
    if not text.strip():
        return []
    if max_chars < 80:
        raise ValueError("max_chars must be >= 80")

    spans = _sentence_spans(text)
    chunks: list[TextChunk] = []
    buf_start: int | None = None
    buf_end = 0

    for start, end in spans:
        if buf_start is None:
            buf_start = start
        if end - buf_start <= max_chars:
            buf_end = end
            continue
        # flush the current buffer, then carry the last sentence as overlap
        if buf_start is not None and buf_end > buf_start:
            chunks.append(_make(text, len(chunks), buf_start, buf_end, evidence_id))
        carried_start = _overlap_start(spans, start, buf_start, overlap_chars)
        buf_start = carried_start if carried_start is not None else start
        buf_end = end
        if buf_end - buf_start > max_chars:  # a single oversized sentence
            for piece_start in range(buf_start, buf_end, max_chars):
                piece_end = min(piece_start + max_chars, buf_end)
                chunks.append(_make(text, len(chunks), piece_start, piece_end, evidence_id))
            buf_start = None
    if buf_start is not None and buf_end > buf_start:
        chunks.append(_make(text, len(chunks), buf_start, buf_end, evidence_id))
    return chunks


def _make(text: str, index: int, start: int, end: int, evidence_id: str) -> TextChunk:
    body = text[start:end].strip()
    return TextChunk(index=index, start=start, end=end, text=body, evidence_id=f"{evidence_id}#{index}")


def _sentence_spans(text: str) -> list[tuple[int, int]]:
    spans: list[tuple[int, int]] = []
    cursor = 0
    for i, char in enumerate(text):
        if char in SENTENCE_END:
            if i + 1 > cursor:
                spans.append((cursor, i + 1))
            cursor = i + 1
    if cursor < len(text):
        spans.append((cursor, len(text)))
    return spans or [(0, len(text))]


def _overlap_start(spans: list[tuple[int, int]], current_start: int, buf_start: int, overlap_chars: int) -> int | None:
    tail_start = None
    for start, end in spans:
        if start >= current_start:
            break
        if end <= current_start and current_start - start <= overlap_chars:
            tail_start = start
    return tail_start
