"""Vendored upstream files are pinned, non-empty and hash-locked."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

VENDOR = Path(__file__).resolve().parents[2] / "vendor" / "linger"


def test_upstream_files_match_lock():
    lock = json.loads((VENDOR / "LOCK.json").read_text(encoding="utf-8"))
    assert lock["commit"] == "6663e21a4f07004def7591b1e07e4725c5c73e60"
    assert len(lock["upstream_files"]) >= 5
    for name, meta in lock["upstream_files"].items():
        path = VENDOR / "upstream" / name
        assert path.exists() and path.stat().st_size == meta["bytes"]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == meta["sha256"]
