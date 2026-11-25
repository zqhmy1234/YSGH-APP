"""B5 merge: MemoryStore backed by backend canonical tables instead of V3 JSON.

The domain ``MemoryStore`` supplies the invariants (conflict precedence, dedup,
change log). This adapter replaces persistence AND closes the gaps found by the
merge review:

  - ``propose`` writes through immediately (a draft that never commits is a
    lost memory — the old nightly CLI was a no-op).
  - ``commit`` maps layers to the backend enum and checks WriteOut.status.
  - ``purge`` deletes backend rows so a later hydrate cannot resurrect them.
  - entry ids are derived from batch hash + index, so retries across processes
    converge on the same canonical row instead of appending duplicates.
"""
from __future__ import annotations

import hashlib
from datetime import UTC, date, datetime
from typing import Any

from adapters.backend_http import BackendHTTP
from harness.contracts import (
    CommitReceipt,
    MemoryLayer,
    PurgeReceipt,
    WriteReceipt,
    WriteState,
)
from memory.ledger import MemoryStore
from ports import PortUnavailable

# Agent 四层 → backend 两层枚举（memory_entries CHECK 只认 narrative/fact_claim）
_LAYER_MAP = {
    MemoryLayer.NARRATIVE.value: "narrative",
    MemoryLayer.FACT.value: "fact_claim",
    MemoryLayer.PREFERENCE.value: "narrative",  # 偏好是叙事的一种，无独立 canonical 层
    MemoryLayer.ASSET.value: "narrative",       # 资产索引存 text，层归叙事
}


def _stable_entry_id(batch_hash: str, index: int) -> str:
    digest = hashlib.sha256(f"{batch_hash}:{index}".encode()).hexdigest()[:12]
    return f"m_{digest}"


class BackendMemoryStore(MemoryStore):
    def __init__(self, backend: BackendHTTP) -> None:
        super().__init__()
        self._backend = backend
        self._synced: set[str] = set()

    def load_state(self, state: dict[str, Any]) -> None:
        super().load_state(state)
        self._synced = set((state.get("facts") or {}).keys())

    def hydrate(self, person_id: str, payload: dict[str, Any] | None = None) -> int:
        """Load recent canonical narrative into memory so Sculptor can dedup.

        Pass ``payload`` from the orchestrator's own B5 read to avoid a second
        GET per turn. NOTE: backend read_snapshot caps at NARRATIVE_LIMIT — this
        is a recent window, not the full ledger.
        """
        if payload is None:
            payload = self._backend.memory(person_id)
        if str(payload.get("status")) == "unavailable":
            return 0
        count = 0
        for item in payload.get("narrative") or ():
            eid = str(item.get("entry_id") or "")
            if not eid or eid in self._facts:
                continue
            vfrom = str(item.get("valid_from") or datetime.now(UTC).date().isoformat())[:10]
            self._facts[eid] = {
                "entry_id": eid,
                "person_id": person_id,
                "layer": "narrative",
                "text": str(item.get("text") or ""),
                "valid_from": vfrom,
                "valid_to": None,
                # entries() parses recorded_at with isoformat — never store ""
                "recorded_at": datetime.combine(
                    date.fromisoformat(vfrom), datetime.min.time(), tzinfo=UTC
                ).isoformat(),
                "superseded_by": None,
                "evidence": list(item.get("evidence") or ()),
                "correction": False,
                "tags": {},
            }
            self._synced.add(eid)
            count += 1
        return count

    def _post_fact(self, person_id: str, entry_id: str, fact: dict[str, Any]) -> None:
        evidence = [
            {"type": "agent_evidence", "id": str(e)}
            for e in (fact.get("evidence") or ())
        ] or [{"type": "agent_evidence", "id": entry_id}]
        result = self._backend.write_memory(
            str(person_id),
            {
                "entry_id": entry_id,
                "person_id": str(person_id),
                "text": str(fact.get("text") or ""),
                "kind": "episode",
                "layer": _LAYER_MAP.get(str(fact.get("layer") or "narrative"), "narrative"),
                "evidence": evidence,
                "valid_from": str(fact.get("valid_from") or datetime.now(UTC).date().isoformat())[:10],
                "summary": str(fact.get("summary") or "")[:120],
                "story_arc": str(fact.get("story_arc") or "")[:200],
                "confidence": 0.6,
                "sensitivity": "normal",
            },
        )
        # 后端可能拒写（证据不足等）：不能当成功，否则入口层会以为记忆已落库
        data = result.get("data", result) if isinstance(result, dict) else {}
        if isinstance(data, dict) and str(data.get("status") or "ok") == "rejected":
            raise PortUnavailable("backend", f"canonical memory rejected: {data.get('reason')}")

    def propose(self, proposal, *, person_id: str):
        """Canonical mode has no offline commit lane: write through immediately.

        Returns a COMMITTED receipt (not DRAFT) so callers can trust the result.
        """
        material = f"propose:{person_id}:{hashlib.sha256(proposal.text.encode()).hexdigest()[:16]}"
        stable_id = _stable_entry_id(material, 0)
        object.__setattr__(proposal, "entry_id", stable_id)
        batch = type("B", (), {
            "batch_hash": stable_id,
            "person_id": person_id,
            "proposals": (proposal,),
        })()
        receipt = self.commit(batch)
        if receipt.rejected:
            return WriteReceipt(state=WriteState.REJECTED, reason="canonical_write_rejected")
        return WriteReceipt(state=WriteState.CONFIRMED, reason="canonical_write_through")

    def commit(self, batch) -> CommitReceipt:
        snapshot = self.dump_state()
        before_synced = set(self._synced)
        # 稳定 entry_id：跨进程重试收敛到同一 canonical 行
        for i, proposal in enumerate(batch.proposals):
            if not getattr(proposal, "entry_id", ""):
                object.__setattr__(proposal, "entry_id", _stable_entry_id(batch.batch_hash, i))
        receipt = super().commit(batch)
        if receipt.rejected or receipt.idempotent or not receipt.committed:
            return receipt
        try:
            for entry_id in receipt.committed:
                fact = self._facts.get(entry_id)
                if fact is None or entry_id in self._synced:
                    continue
                self._post_fact(str(fact["person_id"]), entry_id, fact)
                self._synced.add(entry_id)
            return receipt
        except Exception as exc:
            self.load_state(snapshot)
            self._synced = before_synced
            self._batches.pop(batch.batch_hash, None)
            raise PortUnavailable("backend", "canonical memory write failed") from exc

    def purge(self, scope) -> PurgeReceipt:
        layers = {layer.value for layer in scope.layers}
        target = [
            eid for eid, row in self._facts.items()
            if row["person_id"] == scope.person_id and (not layers or row["layer"] in layers)
        ]
        if scope.entry_ids:
            target = [e for e in target if e in set(scope.entry_ids)]
        for entry_id in target:
            self._backend.delete_memory(scope.person_id, entry_id)
            self._facts.pop(entry_id, None)
            self._synced.discard(entry_id)
        return PurgeReceipt(purged=len(target), layers=scope.layers)


__all__ = ["BackendMemoryStore"]
