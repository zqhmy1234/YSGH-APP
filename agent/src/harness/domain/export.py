"""Export manifest: counts + checksum, generated from the ledger (no hidden fields)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

from harness.contracts import Digest, MemoryEntry


def manifest(person_id: str, entries: Sequence[MemoryEntry], digest: Digest) -> dict[str, object]:
    payload = {
        "person_id": person_id,
        "entries": [entry.model_dump(mode="json") for entry in entries],
        "digest": digest.model_dump(mode="json"),
    }
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "person_id": person_id,
        "entries": len(entries),
        "digest_sections": len(digest.sections),
        "bytes": len(raw),
        "sha256": hashlib.sha256(raw).hexdigest(),
    }
