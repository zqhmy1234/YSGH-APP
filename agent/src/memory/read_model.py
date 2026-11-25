"""B5-02 agent-side snapshot parsing.

The backend owns storage; this module only validates the wire shape and turns it
into frozen objects. A malformed or missing status becomes ``unavailable`` with
a note, never an empty ``ok``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

MemoryStatus = Literal["ok", "insufficient_data", "unavailable"]


@dataclass(frozen=True)
class NarrativeEpisode:
    entry_id: str
    text: str
    evidence: tuple[str, ...] = ()
    valid_from: str = ""
    story_arc: str = ""


@dataclass(frozen=True)
class Correction:
    target_type: str
    target_id: str
    field: str
    value: str


@dataclass(frozen=True)
class MemorySnapshot:
    user_id: str
    status: MemoryStatus
    narrative: tuple[NarrativeEpisode, ...] = ()
    corrections: tuple[Correction, ...] = ()
    digest: dict[str, Any] | None = None
    asset_index: tuple[dict[str, Any], ...] = ()
    note: str = field(default="")


def parse_snapshot(payload: dict[str, Any], *, expected_user: str = "") -> MemorySnapshot:
    status = payload.get("status")
    if status not in {"ok", "insufficient_data", "unavailable"}:
        return MemorySnapshot(
            user_id=expected_user,
            status="unavailable",
            note=f"contract_drift:status={status!r}",
        )
    user_id = str(payload.get("user_id") or expected_user)
    if expected_user and user_id != expected_user:
        return MemorySnapshot(user_id=expected_user, status="unavailable", note="user_mismatch")
    narrative = tuple(
        NarrativeEpisode(
            entry_id=str(item.get("entry_id") or ""),
            text=str(item.get("text") or ""),
            evidence=tuple(str(e) for e in item.get("evidence") or ()),
            valid_from=str(item.get("valid_from") or ""),
            story_arc=str(item.get("story_arc") or ""),
        )
        for item in payload.get("narrative") or ()
        if isinstance(item, dict)
    )
    corrections = tuple(
        Correction(
            target_type=str(item.get("target_type") or ""),
            target_id=str(item.get("target_id") or ""),
            field=str(item.get("field") or ""),
            value=str(item.get("value") or ""),
        )
        for item in payload.get("corrections") or ()
        if isinstance(item, dict)
    )
    return MemorySnapshot(
        user_id=user_id,
        status=status,
        narrative=narrative,
        corrections=corrections,
        digest=payload.get("digest") if isinstance(payload.get("digest"), dict) else None,
        asset_index=tuple(item for item in payload.get("asset_index") or () if isinstance(item, dict)),
        note=str(payload.get("note") or ""),
    )


__all__ = ["Correction", "MemorySnapshot", "MemoryStatus", "NarrativeEpisode", "parse_snapshot"]
