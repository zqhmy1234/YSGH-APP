"""Stable prompt renderer for the RoleCard -> persona segment.

The full 9-segment assembly lives in ``harness.context.ContextAssembler``. This
module only renders stable identity text and guarantees byte-stable output for a
given card version (cache key = role id + card hash + policy version).
"""

from __future__ import annotations

import hashlib

from .role_card import TABOO, RoleCard


def stable_prompt(card: RoleCard, *, policy_version: str = "2026-11-23") -> str:
    text = card.render()
    if not any(phrase in text for phrase in TABOO):
        text += f"\n禁语：{'、'.join(TABOO)}"
    return text.strip()


def stable_cache_key(card: RoleCard, *, policy_version: str = "2026-11-23") -> str:
    material = (
        f"{policy_version}|{card.role_id}|{card.version}|{card.card_hash}|"
        f"{stable_prompt(card, policy_version=policy_version)}"
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


__all__ = ["stable_cache_key", "stable_prompt"]
