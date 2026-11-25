"""Character Card V2 and a SillyTavern-style lorebook, adapted to this app.

Spec: https://github.com/malfoyslastname/character-card-spec-v2
We keep the V2 field names so existing cards import, but a card is *flavour*:
``system_prompt`` never replaces ``platform_guard`` and lorebook text is data,
not instructions. PNG/embedded-card parsing is deliberately out of scope here.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

CARD_SPEC = "chara_card_v2"
CARD_SPEC_VERSION = "2.0"
MAX_CARD_CHARS = 3000
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_OVERRIDE = ("ignore previous", "忽略以上", "无视规则", "disregard the above", "developer mode")


class CardModel(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)


class LorebookEntry(CardModel):
    keys: tuple[str, ...] = ()
    secondary_keys: tuple[str, ...] = ()
    selective: bool = False
    constant: bool = False
    order: int = 10
    position: str = "before_char"
    depth: int = 4
    probability: int = Field(default=100, ge=0, le=100)
    scan_depth: int = 4
    inclusion_group: str = ""
    content: str = Field(default="", max_length=MAX_CARD_CHARS)
    uid: str = ""


class CharacterBook(CardModel):
    name: str = ""
    entries: tuple[LorebookEntry, ...] = ()


class CharacterCard(CardModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=MAX_CARD_CHARS)
    personality: str = Field(default="", max_length=MAX_CARD_CHARS)
    scenario: str = Field(default="", max_length=MAX_CARD_CHARS)
    first_mes: str = Field(default="", max_length=MAX_CARD_CHARS)
    mes_example: str = Field(default="", max_length=MAX_CARD_CHARS)
    creator_notes: str = Field(default="", max_length=MAX_CARD_CHARS)
    system_prompt: str = Field(default="", max_length=MAX_CARD_CHARS)
    post_history_instructions: str = Field(default="", max_length=MAX_CARD_CHARS)
    alternate_greetings: tuple[str, ...] = ()
    character_book: CharacterBook | None = None
    tags: tuple[str, ...] = ()
    creator: str = Field(default="", max_length=100)
    character_version: str = Field(default="", max_length=100)
    extensions: dict[str, Any] = Field(default_factory=dict)
    spec: str = CARD_SPEC
    spec_version: str = CARD_SPEC_VERSION

    @field_validator(
        "description",
        "personality",
        "scenario",
        "first_mes",
        "mes_example",
        "creator_notes",
        "system_prompt",
        "post_history_instructions",
    )
    @classmethod
    def _clean(cls, value: str) -> str:
        return _CONTROL.sub("", value).strip()


class CardError(ValueError):
    pass


def parse_card(payload: str | bytes | dict[str, Any]) -> CharacterCard:
    if isinstance(payload, (str, bytes)):
        try:
            raw = json.loads(payload)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise CardError("card is not valid JSON") from exc
    else:
        raw = payload
    if not isinstance(raw, dict):
        raise CardError("card must be a JSON object")
    data = raw.get("data") if raw.get("spec") == CARD_SPEC and isinstance(raw.get("data"), dict) else raw
    if not isinstance(data, dict):
        raise CardError("card data must be an object")
    known = {
        "name",
        "description",
        "personality",
        "scenario",
        "first_mes",
        "mes_example",
        "creator_notes",
        "system_prompt",
        "post_history_instructions",
        "alternate_greetings",
        "character_book",
        "tags",
        "creator",
        "character_version",
        "extensions",
    }
    body = {key: data[key] for key in known if key in data}
    body["spec"] = raw.get("spec", CARD_SPEC)
    body["spec_version"] = raw.get("spec_version", CARD_SPEC_VERSION)
    if "character_book" in body and isinstance(body["character_book"], dict):
        raw_book = body["character_book"]
        entries = raw_book.get("entries", ())
        if isinstance(entries, list):
            body["character_book"] = {
                "name": str(raw_book.get("name", ""))[:100],
                "entries": [
                    {key: item[key] for key in item if key in LorebookEntry.model_fields}
                    for item in entries
                    if isinstance(item, dict)
                ],
            }
    try:
        card = CharacterCard.model_validate(body)
    except Exception as exc:  # noqa: BLE001 - pydantic detail is surfaceable
        raise CardError(f"card schema invalid: {exc}") from exc
    if card.spec != CARD_SPEC:
        raise CardError(f"unsupported card spec: {card.spec}")
    if card.spec_version != CARD_SPEC_VERSION:
        raise CardError(f"unsupported card spec_version: {card.spec_version}")
    return card


def sanitize_card(card: CharacterCard) -> CharacterCard:
    """Strip control characters and neutralise override markers in card text."""
    updates: dict[str, Any] = {}
    for field in (
        "description",
        "personality",
        "scenario",
        "first_mes",
        "mes_example",
        "creator_notes",
        "system_prompt",
        "post_history_instructions",
    ):
        value = getattr(card, field)
        cleaned = _CONTROL.sub("", value).strip()
        for marker in _OVERRIDE:
            cleaned = re.sub(re.escape(marker), "[已忽略]", cleaned, flags=re.IGNORECASE)
        updates[field] = cleaned
    return card.model_copy(update=updates)


class CardSafetyPolicy:
    """Import-time checks; a card may add flavour but not permissions."""

    def check(self, card: CharacterCard) -> list[str]:
        issues: list[str] = []
        if card.system_prompt.strip():
            issues.append("card_system_prompt_ignored")
        if card.post_history_instructions.strip():
            issues.append("card_post_history_ignored")
        for field in ("description", "personality", "scenario", "first_mes", "mes_example", "system_prompt"):
            if len(getattr(card, field)) > MAX_CARD_CHARS:
                issues.append(f"{field}_too_long")
        lowered = " ".join(
            [card.description, card.personality, card.scenario, card.first_mes, card.mes_example, card.system_prompt]
        ).lower()
        for marker in _OVERRIDE:
            if marker in lowered:
                issues.append(f"override_marker:{marker}")
        return issues


def activate_lorebook(
    book: CharacterBook | None, recent_text: str, *, budget_chars: int = 800, seed: str = ""
) -> tuple[LorebookEntry, ...]:
    """Deterministic World-Info-style activation.

    ``probability`` is resolved with a stable hash, not ``random``: the same
    conversation and seed always inject the same entries, which keeps tests and
    replay honest. ``constant`` bypasses keyword matching; groups keep one.
    """
    if book is None or not book.entries:
        return ()
    text = recent_text.lower()
    matched: list[LorebookEntry] = []
    for entry in book.entries:
        if entry.constant:
            matched.append(entry)
            continue
        if not any(key and key.lower() in text for key in entry.keys):
            continue
        if entry.selective and entry.secondary_keys and not all(key.lower() in text for key in entry.secondary_keys):
            continue
        if entry.probability < 100:
            roll = int(hashlib.sha256(f"{seed}|{entry.uid}|{entry.content}".encode()).hexdigest()[:8], 16) % 100
            if roll >= entry.probability:
                continue
        matched.append(entry)
    grouped: dict[str, LorebookEntry] = {}
    for entry in sorted(matched, key=lambda e: (e.order, e.uid)):
        if entry.inclusion_group:
            grouped.setdefault(entry.inclusion_group, entry)
    selected = list(grouped.values()) if grouped else matched
    if grouped:
        selected = [e for e in matched if not e.inclusion_group or grouped[e.inclusion_group] is e]
    selected = sorted(selected, key=lambda e: (e.order, e.uid))
    out: list[LorebookEntry] = []
    used = 0
    for entry in selected:
        if used + len(entry.content) > budget_chars:
            continue
        out.append(entry)
        used += len(entry.content)
    return tuple(out)


def render_lorebook(entries: Iterable[LorebookEntry]) -> str:
    return "\n".join(f"- {entry.content}" for entry in entries if entry.content)


__all__ = [
    "CARD_SPEC",
    "CARD_SPEC_VERSION",
    "CardError",
    "CardSafetyPolicy",
    "CharacterBook",
    "CharacterCard",
    "LorebookEntry",
    "activate_lorebook",
    "parse_card",
    "render_lorebook",
    "sanitize_card",
]
