"""Six-domain slice: trust / family / ingest / event / echo (+ pointer to memory).

This is the B1-B8 spine minus the parts already implemented elsewhere:
  * B3 retrieval  -> ``agents/librarian.py``
  * B5 memory     -> ``memory/ledger.py`` + ``memory/projection.py``
  * B7 human tone -> ``agents/muse.py`` + ``prompts/persona.md``
"""

from __future__ import annotations

__all__ = ["family", "trust", "ingest", "event", "echo"]
