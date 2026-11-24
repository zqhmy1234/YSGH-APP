#!/usr/bin/env python3
"""B5-03 dry-run: extract candidate names from legacy ``memories.people``.

This script **never writes ``persons``**. Its only job is to show what a future,
explicit user-confirmation UI would need to review. The acceptance probe is that
``auto_created_persons`` is always 0.

Usage:
    python scripts/person_candidates.py [--user-id UUID] [--limit 200]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from app.db.models.memory_agent import Memory  # noqa: E402
from app.db.session import SessionLocal  # noqa: E402
from app.domain.person import name_norm  # noqa: E402
from sqlalchemy import select  # noqa: E402


def _names(raw: Any) -> list[str]:
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    if isinstance(raw, dict):
        raw = raw.get("names") or raw.get("people") or list(raw.values())
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            value = item.get("name") or item.get("display_name") or item.get("text")
            if value:
                out.append(str(value))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--user-id", default="")
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        stmt = select(Memory).where(Memory.people.is_not(None)).limit(max(1, args.limit))
        if args.user_id:
            stmt = stmt.where(Memory.user_id == str(args.user_id))
        rows = list(db.execute(stmt).scalars())
    except Exception as exc:  # noqa: BLE001 —— CLI must surface environment failure
        print(json.dumps({"error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False))
        return 2
    finally:
        db.close()

    candidates: dict[str, dict] = {}
    for row in rows:
        for name in _names(row.people):
            norm = name_norm(name)
            if not norm:
                continue
            candidates.setdefault(
                norm,
                {
                    "name": name.strip(),
                    "name_norm": norm,
                    "source_memory_id": row.id,
                    "status": "needs_user_confirmation",
                },
            )
    print(
        json.dumps(
            {
                "candidates": list(candidates.values()),
                "candidate_count": len(candidates),
                "auto_created_persons": 0,
                "written_to": "none (dry-run)",
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
