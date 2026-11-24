"""Pure profile digest projection; application layer injects the dimensions dict.

Kept separate from ``app.services.profile_digest`` (the L3-facing wrapper) so
the rule can be tested without a FastAPI request or a database session.
"""

from __future__ import annotations

from app.services.profile_digest import (
    DIGEST_MAX_CHARS,
    SECTIONS,
    ProfileDigestOut,
    build_agent_digest,
)


def build_digest(dimensions: dict | None) -> ProfileDigestOut:
    return build_agent_digest(dimensions)


__all__ = ["DIGEST_MAX_CHARS", "ProfileDigestOut", "SECTIONS", "build_digest"]
