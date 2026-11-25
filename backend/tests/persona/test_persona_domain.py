"""B7-01 probes: three written choices, version bump, next-turn prose."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.domain.persona import (
    RELATIONSHIP_QUESTIONS,
    RelationshipChoices,
    apply_update,
    default_snapshot,
    relationship_note,
    snapshot_from_row,
)
from pydantic import ValidationError


def test_out_of_range_is_rejected_not_clamped():
    with pytest.raises(ValidationError):
        RelationshipChoices(initiative=9)
    with pytest.raises(ValidationError):
        RelationshipChoices(density=-1)
    with pytest.raises(ValidationError):
        RelationshipChoices(advice_bias=3)


def test_missing_row_is_default_source_without_error():
    snapshot = default_snapshot("u1")
    assert snapshot.source == "default"
    assert snapshot.version == 0
    assert snapshot.choices.initiative == 1


def test_update_bumps_version_once_and_note_changes():
    row = SimpleNamespace(
        initiative=1,
        density=1,
        advice_bias=1,
        version=4,
        role_id="gentle-friend",
        updated_at=None,
    )
    before = snapshot_from_row("u1", row)
    new_version = apply_update(
        row,
        RelationshipChoices(initiative=2, density=0, advice_bias=2),
        role_id="quiet-listener",
    )
    after = snapshot_from_row("u1", row)
    assert new_version == before.version + 1
    assert after.version == before.version + 1
    assert after.role_id == "quiet-listener"
    assert relationship_note(after.choices) != relationship_note(before.choices)


def test_snapshot_does_not_mutate_row():
    row = SimpleNamespace(
        initiative=0,
        density=2,
        advice_bias=0,
        version=0,
        role_id="gentle-friend",
        updated_at=None,
    )
    snapshot = snapshot_from_row("u1", row)
    assert snapshot.choices.initiative == 0
    assert row.version == 0


def test_relationship_note_is_full_sentences_not_labels():
    note = relationship_note(RelationshipChoices(initiative=0, density=0, advice_bias=0))
    assert "她不会主动开新话题" in note
    assert "她一次只说一两句" in note
    assert "她先不急着给办法" in note
    assert "initiative=" not in note
    assert "density=" not in note
    assert "advice_bias=" not in note


def test_every_question_has_three_full_sentence_options():
    assert len(RELATIONSHIP_QUESTIONS) == 3
    for item in RELATIONSHIP_QUESTIONS:
        assert len(item["options"]) == 3
        for option in item["options"]:
            assert len(option) >= 30
            assert option.endswith("。")


def test_agent_payload_is_prose_only():
    from app.api.chat_helpers import _persona_payload

    class FakeDB:
        def get(self, model, user_id):
            return SimpleNamespace(
                initiative=2,
                density=1,
                advice_bias=0,
                version=4,
                role_id="quiet-listener",
                updated_at=None,
            )

    payload = _persona_payload(FakeDB(), "u1")
    assert payload["role_id"] == "quiet-listener"
    assert payload["version"] == 4
    assert "她会在你停下时先开口" in payload["relationship_note"]
    assert "initiative" not in payload["relationship_note"]
