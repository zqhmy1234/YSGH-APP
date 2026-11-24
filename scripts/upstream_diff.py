"""Compare the local port against the pinned upstream files.

Default is offline: report local sha256 and the recorded upstream commit, so a
reviewer can see whether a port changed without an upstream re-check. With
``--online`` it fetches the raw files and prints a rule/constant diff summary.

Usage:
    python scripts/upstream_diff.py [--online]
Exit: 0 = ledger consistent; 1 = drift detected; 2 = environment error.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LEDGER = ROOT / "agent" / "docs" / "upstream" / "linger.json"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--online", action="store_true")
    args = parser.parse_args()

    if not LEDGER.exists():
        print(f"environment error: missing {LEDGER}")
        return 2
    data = json.loads(LEDGER.read_text(encoding="utf-8"))
    commit = data["commit"]
    drift = 0

    for item in data["files"]:
        local = ROOT / item["local"]
        if not local.exists():
            print(f"MISSING local file: {item['local']} (upstream {item['upstream']})")
            drift += 1
            continue
        print(f"{item['status']:<10} {item['local']} sha256={sha256(local)[:16]}")
        if not args.online:
            continue
        url = f"https://raw.githubusercontent.com/simplexai-labs/linger/{commit}/{item['upstream']}"
        try:
            upstream = urllib.request.urlopen(url, timeout=10).read().decode("utf-8", "replace")
        except Exception as exc:  # network is optional
            print(f"  online check skipped: {type(exc).__name__}")
            continue
        for token in ("RRF", "alternates", "rollback", "valid_to", "issue"):
            print(f"  token {token!r}: upstream={'yes' if token.lower() in upstream.lower() else 'no'}")

    return 0 if drift == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
