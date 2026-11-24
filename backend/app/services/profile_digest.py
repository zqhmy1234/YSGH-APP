"""Profile digest projection for agent context segment #3.

Eight fixed sections, ≤600 chars total, three states. Read-only: this module
never writes. The input is ``user_profile.dimensions`` (sparse JSON), never a
live ORM row, so the projection can be unit-tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

DIGEST_MAX_CHARS = 600
SECTION_MAX_CHARS = 80
STATUS = Literal["ok", "insufficient_data", "unavailable"]
SECTIONS = ("身份", "关系", "习惯", "偏好", "重要事件", "雷区", "近期状态", "未了事项")

# Dimension-name -> section mapping. Unknown dimensions are ignored (fail-closed:
# an unmapped dimension never leaks into a section it does not belong to).
_DIMENSION_SECTION: dict[str, str] = {
    "identity": "身份",
    "basic": "身份",
    "relation_core": "关系",
    "relationships": "关系",
    "habits": "习惯",
    "preference": "偏好",
    "values_priority": "偏好",
    "life_events": "重要事件",
    "important_events": "重要事件",
    "taboo": "雷区",
    "boundaries": "雷区",
    "recent_state": "近期状态",
    "mood": "近期状态",
    "pending": "未了事项",
    "open_loops": "未了事项",
}
# Sections that must never be empty-shaped when data exists but is thin.
_EXPLANATION = {
    "insufficient_data": "该用户尚未提供足够画像，不要假装了解他。",
    "unavailable": "画像服务暂不可用，不要编造。",
}


@dataclass(frozen=True)
class DigestSectionOut:
    name: str
    text: str


@dataclass(frozen=True)
class ProfileDigestOut:
    status: STATUS
    sections: tuple[DigestSectionOut, ...] = ()
    text: str = ""
    chars: int = 0
    note: str = field(default="")


def build_agent_digest(dimensions: dict | None, *, max_chars: int = DIGEST_MAX_CHARS) -> ProfileDigestOut:
    """Build the projection; ``None`` is unavailable, thin data is insufficient."""
    if dimensions is None:
        return ProfileDigestOut(
            status="unavailable",
            text=_EXPLANATION["unavailable"],
            chars=len(_EXPLANATION["unavailable"]),
            note="dimensions_missing",
        )
    if not isinstance(dimensions, dict) or not dimensions:
        return ProfileDigestOut(
            status="insufficient_data",
            text=_EXPLANATION["insufficient_data"],
            chars=len(_EXPLANATION["insufficient_data"]),
            note="no_dimensions",
        )
    buckets: dict[str, list[str]] = {name: [] for name in SECTIONS}
    for key, value in dimensions.items():
        section = _DIMENSION_SECTION.get(str(key))
        if section is None or value in (None, "", [], {}):
            continue
        text = _render_value(value)
        if text:
            buckets[section].append(text)
    if not any(buckets.values()):
        return ProfileDigestOut(
            status="insufficient_data",
            text=_EXPLANATION["insufficient_data"],
            chars=len(_EXPLANATION["insufficient_data"]),
            note="unmapped_dimensions_only",
        )
    sections: list[DigestSectionOut] = []
    for name in SECTIONS:
        if not buckets[name]:
            continue
        text = "·" + "；".join(buckets[name])
        sections.append(DigestSectionOut(name=name, text=text[:SECTION_MAX_CHARS]))
    rendered = "".join(f"{s.name}:{s.text}" for s in sections)
    if len(rendered) > max_chars:
        # Global trim, never per-section truncation that would hide a section.
        scale = max_chars / max(1, len(rendered))
        sections = [DigestSectionOut(name=s.name, text=s.text[: max(12, int(len(s.text) * scale))]) for s in sections]
        rendered = "".join(f"{s.name}:{s.text}" for s in sections)
    return ProfileDigestOut(status="ok", sections=tuple(sections), text=rendered, chars=len(rendered))


def _render_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple, set)):
        return "、".join(str(item).strip() for item in value if str(item).strip())
    if isinstance(value, dict):
        return "、".join(f"{k}:{v}" for k, v in value.items() if str(v).strip())
    return str(value).strip()


__all__ = [
    "DIGEST_MAX_CHARS",
    "ProfileDigestOut",
    "SECTIONS",
    "SECTION_MAX_CHARS",
    "build_agent_digest",
]
