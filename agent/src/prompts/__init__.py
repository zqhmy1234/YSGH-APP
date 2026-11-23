"""Prompt assets are data, not code: loaded by name, cached by process."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

HERE = Path(__file__).resolve().parent


@lru_cache(maxsize=16)
def load(name: str) -> str:
    path = HERE / f"{name}.md"
    if not path.exists():
        raise FileNotFoundError(f"prompt asset missing: {name}")
    return path.read_text(encoding="utf-8").strip()
