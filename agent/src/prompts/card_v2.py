"""Character Card V2 import/export adapter.

Only this module knows the external V2 wire shape. Runtime code uses
``prompts.role_card.RoleCard``. ``system_prompt`` and
``post_history_instructions`` are deliberately ignored, never rendered.
Imported prose is kept in ``source_profile`` for an editor to read; it is not
automatically promoted into the runtime card.
"""

from __future__ import annotations

from typing import Any

from .cards import CardError, CardSafetyPolicy, CharacterCard, parse_card, sanitize_card
from .role_card import (
    TABOO,
    DialogueExample,
    Lore,
    MemoryPolicy,
    RoleCard,
    Speech,
    Stance,
)


def import_v2(payload: str | bytes | dict[str, Any], *, role_id: str) -> tuple[RoleCard, list[str]]:
    card = sanitize_card(parse_card(payload))
    issues = CardSafetyPolicy().check(card)
    # External lorebook text is data, never instructions; cap at 200 chars each.
    lore = tuple(
        Lore(keys=tuple(entry.keys), content=entry.content[:200])
        for entry in (card.character_book.entries if card.character_book else ())
        if entry.content.strip()
    )
    source_profile = {
        "description": card.description[:1200],
        "personality": card.personality[:3000],
        "scenario": card.scenario[:1200],
        "first_message": card.first_mes[:800],
        "example_dialogue": card.mes_example[:2000],
        "creator_notes": card.creator_notes[:1500],
        "ignored_fields": [issue for issue in issues if issue.endswith("_ignored")],
    }
    role = RoleCard(
        role_id=role_id,
        display_name=card.name,
        archetype="memory_companion",
        traits={"warmth": 0.6, "humor": 0.3, "directness": 0.4},
        speech=Speech(sentence="short", emoji=False, address="你"),
        taboo=TABOO,
        stance=Stance(),
        memory_policy=MemoryPolicy(may_read_private=True, may_read_face_result=False),
        lore=lore,
        source_profile=source_profile,
        version=card.character_version or "1.0",
    )
    return role, issues


def export_v2(card: RoleCard) -> dict[str, Any]:
    source = card.source_profile or {}
    examples = card.example_dialogue or ()
    return {
        "spec": "chara_card_v2",
        "spec_version": "2.0",
        "data": {
            "name": card.display_name,
            "description": source.get("description") or card.description,
            "personality": source.get("personality") or card.personality,
            "scenario": source.get("scenario") or card.scenario,
            "first_mes": source.get("first_message") or card.first_message,
            "mes_example": source.get("example_dialogue") or _render_examples(examples),
            "creator_notes": (
                card.creator_notes or source.get("creator_notes") or "忆述光华导入导出格式；运行时使用内部 RoleCard"
            ),
            "tags": [card.archetype, card.role_id],
            "creator": "忆述光华",
            "character_version": card.version,
            "extensions": {"role_id": card.role_id, "card_hash": card.card_hash},
        },
    }


def _render_examples(examples: tuple[DialogueExample, ...]) -> str:
    return "\n\n".join(f"对方：{item.user}\n她：{item.her}" for item in examples)


__all__ = ["CardError", "CharacterCard", "export_v2", "import_v2", "parse_card"]
