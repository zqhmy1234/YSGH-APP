"""B5-01 reconciliation between old ``memories`` and canonical entries.

This module is deliberately storage-free: it compares two text lists and returns
counts, hashes, a diff ratio and a small sample. The CLI in
``scripts/memory_reconcile.py`` loads the rows and exits non-zero when the ratio
strictly exceeds the threshold (default 0.1%).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256

from app.domain.memory.ledger import normalize_text


@dataclass(frozen=True)
class ReconcileReport:
    old_count: int
    new_count: int
    matched_texts: int
    missing_in_new: int
    missing_in_old: int
    diff_count: int
    diff_ratio: float
    sample_diff: tuple[str, ...] = ()
    old_hashes: tuple[str, ...] = ()
    new_hashes: tuple[str, ...] = ()
    threshold: float = 0.001

    @property
    def within_threshold(self) -> bool:
        return self.diff_ratio <= self.threshold


def text_hash(text: str) -> str:
    return sha256(normalize_text(text).encode()).hexdigest()


def reconcile_texts(
    old_texts: list[str],
    new_texts: list[str],
    *,
    sample_limit: int = 3,
    threshold: float = 0.001,
) -> ReconcileReport:
    old_hashes = [text_hash(text) for text in old_texts if text.strip()]
    new_hashes = [text_hash(text) for text in new_texts if text.strip()]
    old_counter = Counter(old_hashes)
    new_counter = Counter(new_hashes)

    matched = sum((old_counter & new_counter).values())
    missing_in_new = sum((old_counter - new_counter).values())
    missing_in_old = sum((new_counter - old_counter).values())
    diff_count = missing_in_new + missing_in_old
    denominator = max(1, len(old_hashes), len(new_hashes))
    ratio = diff_count / denominator

    samples: list[str] = []
    for text in old_texts:
        if not text.strip():
            continue
        digest = text_hash(text)
        if old_counter[digest] > new_counter[digest]:
            samples.append(f"old_only:{_clip(text)}")
            if len(samples) >= sample_limit:
                break
    for text in new_texts:
        if not text.strip():
            continue
        digest = text_hash(text)
        if new_counter[digest] > old_counter[digest]:
            samples.append(f"new_only:{_clip(text)}")
            if len(samples) >= sample_limit:
                break

    return ReconcileReport(
        old_count=len(old_hashes),
        new_count=len(new_hashes),
        matched_texts=matched,
        missing_in_new=missing_in_new,
        missing_in_old=missing_in_old,
        diff_count=diff_count,
        diff_ratio=ratio,
        sample_diff=tuple(samples),
        old_hashes=tuple(old_hashes[:16]),
        new_hashes=tuple(new_hashes[:16]),
        threshold=threshold,
    )


def _clip(text: str, limit: int = 80) -> str:
    return normalize_text(text)[:limit]


__all__ = ["ReconcileReport", "reconcile_texts", "text_hash"]
