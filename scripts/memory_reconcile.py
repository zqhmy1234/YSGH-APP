#!/usr/bin/env python3
"""B5-01 reconcile: old ``memories`` vs canonical ``memory_entries``.

Usage:
    python scripts/memory_reconcile.py --user-id <uuid> [--threshold 0.001] [--sample 3]

Exit codes:
    0  within threshold (or both sides empty)
    1  diff ratio strictly above threshold
    2  environment/DB error
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.models.memory import MemoryEntry  # noqa: E402
from app.db.models.memory_agent import Memory  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domain.memory.reconcile import reconcile_texts  # noqa: E402
from sqlalchemy import select  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--threshold", type=float, default=0.001)
    parser.add_argument("--sample", type=int, default=3)
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        old_texts = list(
            db.execute(
                select(Memory.content).where(Memory.user_id == str(args.user_id))
            ).scalars()
        )
        new_texts = list(
            db.execute(
                select(MemoryEntry.text).where(
                    MemoryEntry.user_id == str(args.user_id),
                    MemoryEntry.layer == "narrative",
                    MemoryEntry.valid_to.is_(None),
                )
            ).scalars()
        )
    except Exception as exc:  # noqa: BLE001 —— CLI must distinguish env failure from a real diff
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    finally:
        db.close()

    report = reconcile_texts(
        [text for text in old_texts if text],
        [text for text in new_texts if text],
        sample_limit=args.sample,
        threshold=args.threshold,
    )
    payload = asdict(report)
    payload["within_threshold"] = report.within_threshold
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if report.within_threshold else 1


if __name__ == "__main__":
    raise SystemExit(main())
