"""Four-layer memory ledger: pure domain logic, no database driver here.

Semantics ported from Linger ``apps/node-services/src/memory/apply.ts`` @6663e21:
  * validate the whole batch first, write nothing on a single failure;
  * snapshot before writing and restore on failure;
  * the change log is written by the application, never by the model.

Persistence lives in ``adapters/sqlite_memory.py``: this class only knows dicts
and therefore stays testable and swappable (PG in production, SQLite locally).
"""

from __future__ import annotations

import copy
import uuid
from datetime import date, datetime, timezone
from typing import Any, Sequence

from ..contracts import (
    CommitReceipt,
    MemoryEntry,
    MemoryLayer,
    MemoryProposal,
    OpKind,
    TagSet,
    ProposalBatch,
    PurgeReceipt,
    PurgeScope,
    WriteReceipt,
    WriteState,
)
from ..ports import ConflictError


class MemoryStore:
    def __init__(self) -> None:
        self._facts: dict[str, dict[str, Any]] = {}
        self._corrections: list[dict[str, Any]] = []
        self._log: list[dict[str, Any]] = []
        self._batches: dict[str, str] = {}
        self._drafts: list[dict[str, Any]] = []
        self._links: list[dict[str, Any]] = []

    # -- persistence hooks (overridden by the adapter) ---------------
    def _after_change(self) -> None:  # pragma: no cover - adapter hook
        return None

    def dump_state(self) -> dict[str, Any]:
        return {
            "facts": copy.deepcopy(self._facts),
            "corrections": copy.deepcopy(self._corrections),
            "log": copy.deepcopy(self._log),
            "batches": dict(self._batches),
            "drafts": copy.deepcopy(self._drafts),
            "links": copy.deepcopy(self._links),
        }

    def load_state(self, state: dict[str, Any]) -> None:
        self._facts = copy.deepcopy(state.get("facts", {}))
        self._corrections = copy.deepcopy(state.get("corrections", []))
        self._log = copy.deepcopy(state.get("log", []))
        self._batches = dict(state.get("batches", {}))
        self._drafts = copy.deepcopy(state.get("drafts", []))
        self._links = copy.deepcopy(state.get("links", []))

    # -- corrections --------------------------------------------------
    def add_correction(self, person_id: str, *, field: str, value: str, entry_id: str | None = None) -> None:
        self._corrections.append(
            {"person_id": person_id, "entry_id": entry_id, "field": field, "value": value, "created_at": datetime.now(timezone.utc).isoformat()}
        )
        self._after_change()

    def corrections(self, person_id: str) -> list[dict[str, Any]]:
        return [row for row in self._corrections if row["person_id"] == person_id]

    def _is_corrected(self, person_id: str, text: str) -> bool:
        return any(row["field"] == "text" and row["value"].strip() == text.strip() for row in self.corrections(person_id))

    # -- links ---------------------------------------------------------
    def add_link(self, person_id: str, *, memory_id: str, collection_id: str, note: str = "", created_at: str = "") -> dict[str, Any]:
        row = {
            "person_id": person_id,
            "memory_id": memory_id,
            "collection_id": collection_id,
            "note": note,
            "created_at": created_at or datetime.now(timezone.utc).isoformat(),
        }
        self._links.append(row)
        self._after_change()
        return row

    def links(self, person_id: str) -> list[dict[str, Any]]:
        return [dict(row) for row in self._links if row["person_id"] == person_id]

    # -- propose (turn time: never confirmed) ------------------------
    def propose(self, proposal: MemoryProposal, *, person_id: str) -> WriteReceipt:
        if self._is_corrected(person_id, proposal.text):
            return WriteReceipt(state=WriteState.REJECTED, reason="correction_precedence")
        if proposal.layer in (MemoryLayer.FACT, MemoryLayer.PREFERENCE, MemoryLayer.ASSET) and not proposal.evidence:
            return WriteReceipt(state=WriteState.REJECTED, reason="missing_evidence")
        self._drafts.append(
            {
                "person_id": person_id,
                "layer": proposal.layer.value,
                "text": proposal.text,
                "evidence": list(proposal.evidence),
                "reason": proposal.reason,
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._after_change()
        return WriteReceipt(state=WriteState.DRAFT, reason=proposal.reason or "queued for nightly commit")

    # -- commit (offline batch) --------------------------------------
    def commit(self, batch: ProposalBatch) -> CommitReceipt:
        prior = self._batches.get(batch.batch_hash)
        if prior is not None:
            stored = CommitReceipt.model_validate_json(prior)
            return stored.model_copy(update={"idempotent": True})

        snapshot = self.dump_state()
        recorded_at = datetime.now(timezone.utc)
        committed: list[str] = []
        try:
            for proposal in batch.proposals:
                if self._is_corrected(batch.person_id, proposal.text):
                    raise ConflictError("ledger", "correction_precedence")
                committed.append(self._apply_one(batch.person_id, batch.batch_hash, proposal, recorded_at))
            receipt = CommitReceipt(batch_hash=batch.batch_hash, committed=tuple(committed))
            self._batches[batch.batch_hash] = receipt.model_dump_json()
            self._drafts = [d for d in self._drafts if d["person_id"] != batch.person_id]
            self._after_change()
            return receipt
        except ConflictError:
            self.load_state(snapshot)
            return CommitReceipt(
                batch_hash=batch.batch_hash,
                rejected=((f"batch:{batch.batch_hash}", "correction_precedence"),),
                rolled_back=True,
            )

    def _apply_one(self, person_id: str, batch_hash: str, proposal: MemoryProposal, recorded_at: datetime) -> str:
        if proposal.op is OpKind.ADD:
            entry_id = proposal.entry_id or f"m_{uuid.uuid4().hex[:12]}"
            self._facts[entry_id] = {
                "entry_id": entry_id,
                "person_id": person_id,
                "layer": proposal.layer.value,
                "text": proposal.text,
                "valid_from": (proposal.valid_from or recorded_at.date()).isoformat(),
                "valid_to": None,
                "recorded_at": recorded_at.isoformat(),
                "superseded_by": None,
                "evidence": list(proposal.evidence),
                "correction": False,
                "tags": proposal.tags.model_dump(),
            }
            self._append_log(person_id, batch_hash, entry_id, "add", "", proposal.text, proposal.reason, proposal.evidence)
        elif proposal.op is OpKind.UPDATE:
            old = self._facts.get(proposal.entry_id or "")
            if old is None:
                raise ConflictError("ledger", f"update target missing: {proposal.entry_id}")
            entry_id = f"m_{uuid.uuid4().hex[:12]}"
            self._facts[entry_id] = {
                "entry_id": entry_id,
                "person_id": person_id,
                "layer": proposal.layer.value,
                "text": proposal.text,
                "valid_from": (proposal.valid_from or recorded_at.date()).isoformat(),
                "valid_to": None,
                "recorded_at": recorded_at.isoformat(),
                "superseded_by": None,
                "evidence": list(proposal.evidence),
                "correction": False,
                "tags": proposal.tags.model_dump(),
            }
            self._close(proposal.entry_id, when=(proposal.valid_from or recorded_at.date()), by=entry_id)
            self._append_log(person_id, batch_hash, entry_id, "update", old["text"], proposal.text, proposal.reason, proposal.evidence)
        elif proposal.op is OpKind.RETRACT:
            if not proposal.entry_id:
                raise ConflictError("ledger", "retract without entry_id")
            self._close(proposal.entry_id, when=(proposal.valid_from or recorded_at.date()), by=None)
            self._append_log(person_id, batch_hash, proposal.entry_id, "retract", "", "", proposal.reason, proposal.evidence)
            entry_id = proposal.entry_id
        else:  # CAME_FROM: attach evidence only, never rewrite text
            row = self._facts.get(proposal.entry_id or "")
            if row is None:
                raise ConflictError("ledger", "came_from target missing")
            row["evidence"] = sorted(set(row["evidence"]) | set(proposal.evidence))
            self._append_log(person_id, batch_hash, proposal.entry_id or "", "came_from", "", "", proposal.reason, proposal.evidence)
            entry_id = proposal.entry_id or ""
        return entry_id

    def _close(self, entry_id: str | None, *, when: date, by: str | None) -> None:
        row = self._facts[entry_id or ""]
        if row["valid_to"] is None:
            row["valid_to"] = when.isoformat()
            row["superseded_by"] = by

    def _append_log(self, person_id: str, batch_hash: str, entry_id: str, op: str, before: str, after: str, reason: str, evidence: Sequence[str]) -> None:
        self._log.append(
            {
                "person_id": person_id,
                "batch_hash": batch_hash,
                "entry_id": entry_id,
                "op": op,
                "before": before,
                "after": after,
                "reason": reason,
                "evidence": list(evidence),
                "at": datetime.now(timezone.utc).isoformat(),
            }
        )

    # -- purge -------------------------------------------------------
    def purge(self, scope: PurgeScope) -> PurgeReceipt:
        layers = {layer.value for layer in scope.layers}
        target = [
            entry_id
            for entry_id, row in self._facts.items()
            if row["person_id"] == scope.person_id and (not layers or row["layer"] in layers)
        ]
        if scope.entry_ids:
            target = [e for e in target if e in set(scope.entry_ids)]
        for entry_id in target:
            self._facts.pop(entry_id, None)
        self._after_change()
        return PurgeReceipt(purged=len(target), layers=scope.layers)

    # -- read --------------------------------------------------------
    def entries(self, person_id: str, layers: Sequence[str] | None = None) -> list[MemoryEntry]:
        rows = [
            self._to_entry(row)
            for row in self._facts.values()
            if row["person_id"] == person_id and row["valid_to"] is None and (not layers or row["layer"] in set(layers))
        ]
        return rows

    def _to_entry(self, row: dict[str, Any]) -> MemoryEntry:
        return MemoryEntry(
            entry_id=row["entry_id"],
            person_id=row["person_id"],
            layer=MemoryLayer(row["layer"]),
            text=row["text"],
            valid_from=date.fromisoformat(row["valid_from"]),
            valid_to=date.fromisoformat(row["valid_to"]) if row["valid_to"] else None,
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            superseded_by=row["superseded_by"],
            evidence=tuple(row["evidence"]),
            correction=bool(row["correction"]),
            tags=TagSet(**row.get("tags", {})),
        )

    def digest_facts(self, person_id: str, limit: int = 40) -> list[MemoryEntry]:
        rows = sorted(self.entries(person_id), key=lambda e: e.valid_from, reverse=True)
        return rows[:limit]

    def history(self, entry_id: str) -> list[MemoryEntry]:
        chain: list[MemoryEntry] = []
        cur = self._facts.get(entry_id)
        while cur is not None:
            chain.append(self._to_entry(cur))
            nxt = cur["superseded_by"]
            cur = self._facts.get(nxt) if nxt else None
        return chain

    def change_log(self, person_id: str) -> list[dict[str, Any]]:
        return [row for row in self._log if row["person_id"] == person_id]

    def close(self) -> None:
        return None
