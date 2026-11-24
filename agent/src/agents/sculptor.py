"""Sculptor: the only writer agent, and it writes only through batches.

Offline lane (ported from Linger ``memory-agent.ts`` + ``apply.ts`` @6663e21):
propose -> validate every proposal -> one transaction -> rollback on any failure.
There is no partial success path: a single bad item voids the batch.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, date, datetime

from harness.contracts import (
    AgentName,
    CommitReceipt,
    MemoryLayer,
    MemoryProposal,
    OpKind,
    ProposalBatch,
    TagSet,
)
from memory.ledger import MemoryStore

# 19 issue codes, mirrored from Linger ``wiki-candidate-validation.ts``.
ISSUE_CODES: frozenset[str] = frozenset(
    {
        "missing_evidence",
        "unknown_evidence",
        "empty_text",
        "text_too_long",
        "bad_layer",
        "update_target_missing",
        "retract_target_missing",
        "came_from_target_missing",
        "correction_override",
        "date_order",
        "valid_from_future",
        "duplicate_entry",
        "no_person_scope",
        "batch_hash_collision",
        "layer_disallowed",
        "evidence_scope",
        "ambiguous_time",
        "unsupported_claim",
        "text_normalized_empty",
    }
)


class Sculptor:
    name = AgentName.SCULPTOR
    may_write = True

    def __init__(self, store: MemoryStore, today: Callable[[], date] = lambda: datetime.now(UTC).date()) -> None:
        self._store = store
        self._today = today

    # -- propose ------------------------------------------------------
    def propose_from_episodes(self, person_id: str, episodes: Iterable[dict]) -> list[MemoryProposal]:
        proposals: list[MemoryProposal] = []
        for ep in episodes:
            text = str(ep.get("text", "")).strip()
            if not text:
                continue
            layer = MemoryLayer(ep.get("layer", MemoryLayer.NARRATIVE.value))
            evidence = tuple(str(e) for e in ep.get("evidence", ()))
            entry_id = ep.get("entry_id")
            tags = _tags_of(ep.get("tags"))

            if ep.get("retract"):
                proposals.append(
                    MemoryProposal(
                        op=OpKind.RETRACT,
                        layer=layer,
                        text=text,
                        entry_id=entry_id,
                        evidence=evidence,
                        reason="episode_retracted",
                        tags=tags,
                    )
                )
                continue
            if entry_id:
                proposals.append(
                    MemoryProposal(
                        op=OpKind.UPDATE,
                        layer=layer,
                        text=text,
                        entry_id=entry_id,
                        evidence=evidence,
                        valid_from=_parse_day(ep.get("day")),
                        reason="episode_supersedes",
                        tags=tags,
                    )
                )
                continue
            twin = self._find_active(person_id, text)
            if twin is not None:
                proposals.append(
                    MemoryProposal(
                        op=OpKind.CAME_FROM,
                        layer=layer,
                        text=text,
                        entry_id=twin.entry_id,
                        evidence=evidence,
                        reason="same_text_new_evidence",
                        tags=tags,
                    )
                )
                continue
            proposals.append(
                MemoryProposal(
                    op=OpKind.ADD,
                    layer=layer,
                    text=text,
                    evidence=evidence,
                    valid_from=_parse_day(ep.get("day")),
                    reason="episode_new",
                    tags=tags,
                )
            )
        return proposals

    def _find_active(self, person_id: str, text: str):
        for entry in self._store.entries(person_id):
            if _norm(entry.text) == _norm(text):
                return entry
        return None

    # -- validate -----------------------------------------------------
    def validate(
        self, person_id: str, proposals: Sequence[MemoryProposal], *, allowed_evidence: set[str] | None = None
    ) -> list[tuple[int, str]]:
        issues: list[tuple[int, str]] = []
        if not person_id:
            issues.append((0, "no_person_scope"))
        seen_texts: dict[str, int] = {}
        today = self._today()
        for idx, p in enumerate(proposals):
            if not p.text.strip():
                issues.append((idx, "empty_text"))
            elif not _norm(p.text):
                issues.append((idx, "text_normalized_empty"))
            elif len(p.text) > 400:
                issues.append((idx, "text_too_long"))
            if p.layer is MemoryLayer.ASSET and p.op is OpKind.UPDATE:
                issues.append((idx, "layer_disallowed"))
            if p.tags.library == "knowledge" and p.layer is not MemoryLayer.ASSET:
                issues.append((idx, "layer_disallowed"))
            if p.tags.library == "personal" and p.layer is MemoryLayer.ASSET:
                issues.append((idx, "layer_disallowed"))
            if (
                p.op in (OpKind.ADD, OpKind.UPDATE)
                and p.layer in (MemoryLayer.FACT, MemoryLayer.PREFERENCE)
                and not p.evidence
            ):
                issues.append((idx, "missing_evidence"))
            if allowed_evidence is not None:
                for e in p.evidence:
                    if e not in allowed_evidence:
                        issues.append((idx, "unknown_evidence"))
                        break
            if p.op is OpKind.UPDATE and not p.entry_id:
                issues.append((idx, "update_target_missing"))
            if p.op is OpKind.RETRACT and not p.entry_id:
                issues.append((idx, "retract_target_missing"))
            if p.op is OpKind.CAME_FROM and not p.entry_id:
                issues.append((idx, "came_from_target_missing"))
            if p.valid_from and p.valid_from > today:
                issues.append((idx, "valid_from_future"))
            key = _norm(p.text)
            if key in seen_texts and p.op is OpKind.ADD:
                issues.append((idx, "duplicate_entry"))
            seen_texts.setdefault(key, idx)
        return issues

    # -- apply ------------------------------------------------------
    def apply(self, batch: ProposalBatch, *, allowed_evidence: set[str] | None = None) -> CommitReceipt:
        issues = self.validate(batch.person_id, batch.proposals, allowed_evidence=allowed_evidence)
        if issues:
            return CommitReceipt(
                batch_hash=batch.batch_hash,
                rejected=tuple((f"idx:{i}", code) for i, code in issues),
                rolled_back=True,
            )
        return self._store.commit(batch)


def _tags_of(value: object) -> TagSet:
    if isinstance(value, TagSet):
        return value
    if isinstance(value, dict):
        return TagSet(**value)
    return TagSet()


def _norm(text: str) -> str:
    keep = [ch for ch in text if not ch.isspace() and ch not in "，。、；：！？,.;:!?\u3000"]
    return "".join(keep).lower()


def _parse_day(value: object) -> date | None:
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value:
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None
