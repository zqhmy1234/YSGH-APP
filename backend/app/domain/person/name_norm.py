"""Name normalization shared by persons/content_persons and search filters."""

from __future__ import annotations

import re
import unicodedata

_WS = re.compile(r"\s+")
_PUNCT = re.compile(r"[·•.。，,、;；:：!！?？\"'“”‘’()（）\[\]【】<>《》]")

RELATIONS = frozenset({"family", "friend", "colleague", "classmate", "other"})


def normalize_name(name: str) -> str:
    """NFKC + strip punctuation/whitespace; empty input is never a valid person."""
    text = unicodedata.normalize("NFKC", name or "").strip().lower()
    text = _PUNCT.sub("", text)
    text = _WS.sub("", text)
    return text[:80]


def valid_relation(value: str) -> bool:
    return value in RELATIONS


__all__ = ["RELATIONS", "normalize_name", "valid_relation"]
