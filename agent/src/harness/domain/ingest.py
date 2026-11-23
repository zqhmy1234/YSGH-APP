"""Ingest domain: raw fragments become episodes, or are rejected with a reason.

Nothing enters memory without passing here: blank, oversized, duplicate and
critical items are refused before they can reach the Sculptor batch.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime

from harness.contracts import Episode, IngestReceipt, Sensitivity, SourceKind

from .tagging import TaggingInput, classify_tags
from .trust import classify


class Ingestor:
    def __init__(self, today: date | None = None) -> None:
        self._today = today

    def _now_day(self) -> date:
        return self._today or datetime.now(UTC).date()

    def ingest(self, person_id: str, items: Iterable[Mapping[str, object]]) -> tuple[list[Episode], IngestReceipt]:
        accepted: list[Episode] = []
        rejected: dict[str, int] = {}
        seen: set[str] = set()

        for raw in items:
            text = str(raw.get("text") or "").strip()
            if not text:
                rejected["empty_text"] = rejected.get("empty_text", 0) + 1
                continue
            if len(text) > 2000:
                rejected["too_long"] = rejected.get("too_long", 0) + 1
                continue
            try:
                source = SourceKind(str(raw.get("source") or "text"))
            except ValueError:
                rejected["bad_source"] = rejected.get("bad_source", 0) + 1
                continue
            day = _parse_day(raw.get("day")) or self._now_day()
            place = str(raw.get("place") or "")[:64]
            people = tuple(str(p) for p in (raw.get("people") or ()) if str(p).strip())
            evidence_id = str(raw.get("evidence_id") or f"raw_{_digest(text)}")
            origin = str(
                raw.get("origin")
                or (
                    "knowledge"
                    if str(raw.get("library") or "") == "knowledge"
                    else ("external" if raw.get("external") else "")
                )
            )
            tagged = classify_tags(
                TaggingInput(
                    text=text,
                    source=source.value,
                    origin=origin,
                    has_image=bool(raw.get("has_image")) or source is SourceKind.PHOTO,
                    people=people,
                    place=place,
                )
            )

            fingerprint = _digest(f"{person_id}|{day.isoformat()}|{source.value}|{_norm(text)}")
            if fingerprint in seen:
                rejected["duplicate"] = rejected.get("duplicate", 0) + 1
                continue
            seen.add(fingerprint)

            sensitivity = classify(text, people)
            if sensitivity is Sensitivity.CRITICAL:
                rejected["critical_requires_consent"] = rejected.get("critical_requires_consent", 0) + 1
                continue

            accepted.append(
                Episode(
                    episode_id=f"e_{fingerprint[:12]}",
                    person_id=person_id,
                    source=source,
                    text=text,
                    day=day,
                    place=place,
                    people=people,
                    evidence_id=evidence_id,
                    sensitivity=sensitivity,
                    tags=tagged.tags,
                )
            )

        receipt = IngestReceipt(accepted=tuple(e.episode_id for e in accepted), rejected=tuple(rejected.items()))
        return accepted, receipt


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _norm(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace())


def _parse_day(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
