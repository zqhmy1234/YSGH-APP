"""Prompt assets: manifest, golden snapshot and three safe example cards."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agentv3.prompts.bundle import BUNDLE_BUDGET, PROTECTED, SEGMENT_ORDER, build_bundle, joined
from agentv3.prompts.cards import CardSafetyPolicy, parse_card

ASSETS = Path(__file__).resolve().parents[2] / "src" / "agentv3" / "prompts"
MANIFEST = ASSETS / "manifest.json"
GOLDEN = ASSETS / "golden" / "prompt_bundle.json"
EXAMPLES = ASSETS / "example_cards"


def test_manifest_matches_code_order_and_budget():
    data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    assert [row["name"] for row in data["segments"]] == list(SEGMENT_ORDER)
    assert {row["name"] for row in data["segments"] if row["protected"]} == set(PROTECTED)
    assert data["bundle_budget"] == BUNDLE_BUDGET
    assert data["system_hard_limit"] == 7900


def test_golden_bundle_is_byte_stable():
    data = json.loads(GOLDEN.read_text(encoding="utf-8"))
    bundle = build_bundle()
    text = joined(bundle)
    assert data["total_chars"] == bundle.chars
    assert data["render_sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert [row["name"] for row in data["segments"]] == list(SEGMENT_ORDER)
    assert data["segments"][0]["name"] == "platform_guard"
    assert data["segments"][0]["protected"] is True


def test_three_example_cards_are_parseable_and_safe():
    files = sorted(EXAMPLES.glob("*.json"))
    assert len(files) == 3
    names = set()
    for path in files:
        card = parse_card(path.read_text(encoding="utf-8"))
        names.add(card.name)
        assert card.system_prompt == ""
        assert card.post_history_instructions == ""
        assert CardSafetyPolicy().check(card) == []
    assert names == {"小忆", "老周", "阿嬷"}


def test_gentle_friend_card_keeps_lorebook():
    card = parse_card((EXAMPLES / "gentle-friend.json").read_text(encoding="utf-8"))
    assert card.character_book is not None
    assert len(card.character_book.entries) == 2
