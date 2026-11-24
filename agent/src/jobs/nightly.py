"""Nightly Sculptor batch: one deterministic batch per person per day.

Runs offline (cron/queue), never in the turn path. Same input twice produces the
same ``batch_hash``, so a retried cron job is a no-op rather than a duplicate.
"""

from __future__ import annotations

import hashlib
import json
import sys
from datetime import UTC, date, datetime
from pathlib import Path

from agents.sculptor import Sculptor
from harness.contracts import CommitReceipt, ProposalBatch
from memory.ledger import MemoryStore


class NightlyJob:
    def __init__(self, store: MemoryStore, sculptor: Sculptor | None = None) -> None:
        self._store = store
        self._sculptor = sculptor or Sculptor(store)

    @staticmethod
    def batch_hash(person_id: str, day: date, texts: list[str]) -> str:
        material = json.dumps(
            {"p": person_id, "d": day.isoformat(), "t": sorted(texts)}, ensure_ascii=False, sort_keys=True
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]

    def run(
        self, person_id: str, episodes: list[dict], *, day: date | None = None, allowed_evidence: set[str] | None = None
    ) -> CommitReceipt:
        day = day or datetime.now(UTC).date()
        proposals = self._sculptor.propose_from_episodes(person_id, episodes)
        if not proposals:
            return CommitReceipt(batch_hash=self.batch_hash(person_id, day, []))
        batch = ProposalBatch(
            person_id=person_id,
            batch_hash=self.batch_hash(person_id, day, [p.text for p in proposals]),
            proposals=tuple(proposals),
        )
        return self._sculptor.apply(batch, allowed_evidence=allowed_evidence)


def main(argv: list[str]) -> int:  # pragma: no cover - CLI wiring
    if len(argv) != 2:
        print("usage: python -m jobs.nightly <episodes.json>")
        return 2
    payload = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    store = MemoryStore()
    receipt = NightlyJob(store).run(payload["person_id"], payload["episodes"])
    print(json.dumps(receipt.model_dump(), ensure_ascii=False))
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main(sys.argv))
