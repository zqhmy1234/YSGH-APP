"""Digest projection: eight sections, 600 characters, three states.

The digest is read-model only (CQRS-lite): it never writes, and a missing input
reports ``unavailable`` instead of an empty-looking success.
"""

from __future__ import annotations

from ..contracts import DIGEST_MAX_CHARS, Digest, DigestSection, DigestSection as _DS, MemoryEntry, MemoryLayer

SECTIONS = ("身份", "关系", "习惯", "偏好", "重要事件", "雷区", "近期状态", "未了事项")

_LAYER_TO_SECTIONS: dict[MemoryLayer, tuple[str, ...]] = {
    MemoryLayer.FACT: ("身份", "关系", "习惯", "重要事件"),
    MemoryLayer.NARRATIVE: ("近期状态", "未了事项"),
    MemoryLayer.PREFERENCE: ("偏好", "雷区"),
    MemoryLayer.ASSET: ("重要事件",),
}


def build(entries: list[MemoryEntry] | None, person_id: str, budget_chars: int = DIGEST_MAX_CHARS) -> Digest:
    if entries is None:
        return Digest(person_id=person_id, state="unavailable")
    if len(entries) < 3:
        return Digest(person_id=person_id, state="insufficient_data")

    buckets: dict[str, list[str]] = {name: [] for name in SECTIONS}
    for entry in sorted(entries, key=lambda e: e.valid_from, reverse=True):
        for section in _LAYER_TO_SECTIONS.get(entry.layer, ("重要事件",)):
            if len(buckets[section]) < 3:
                buckets[section].append(entry.text)

    per_section = max(40, budget_chars // max(1, len(SECTIONS)))
    sections: list[_DS] = []
    for name in SECTIONS:
        if not buckets[name]:
            continue
        text = "·" + "；".join(buckets[name])
        sections.append(DigestSection(name=name, text=text[:per_section]))

    digest = Digest(person_id=person_id, sections=tuple(sections), state="ok")
    if digest.chars > budget_chars:  # global trim, never per-section only
        scale = budget_chars / digest.chars
        trimmed = tuple(DigestSection(name=s.name, text=s.text[: max(20, int(len(s.text) * scale))]) for s in sections)
        digest = Digest(person_id=person_id, sections=trimmed, state="ok")
    return digest
