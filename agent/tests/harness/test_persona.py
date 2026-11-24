"""PersonaPort: three relationship choices become prose; no global mutation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from harness.persona import DefaultPersona, PersonaSnapshot, RelationshipChoices


def test_backend_persona_payload_maps_to_snapshot(monkeypatch):
    from adapters.backend_http import BackendHTTP

    adapter = BackendHTTP(base_url="http://backend", user_id="p1")
    monkeypatch.setattr(
        adapter,
        "_get",
        lambda path, **_kw: {
            "user_id": "p1",
            "choices": {"initiative": 2, "density": 1, "advice_bias": 0},
            "role_id": "quiet-listener",
            "relationship_note": "她先开口，但不急着填满安静。",
            "version": 3,
            "updated_at": "2026-11-23T10:00:00+08:00",
            "source": "user",
            "directive": "ignored",
        },
    )
    snapshot = adapter.persona("p1")
    assert snapshot.version == 3
    assert snapshot.choices.density == 1
    assert snapshot.relationship_note.startswith("她先开口")
    assert snapshot.source == "user"
    assert snapshot.updated_at is not None


def test_choices_are_three_positions_only():
    with pytest.raises(ValidationError):
        RelationshipChoices(initiative=9)
    with pytest.raises(ValidationError):
        RelationshipChoices(density=3)


def test_default_snapshot_never_needs_io():
    snapshot = DefaultPersona().snapshot("p1")
    assert snapshot.source == "default"
    assert snapshot.choices.initiative == 0
    assert snapshot.relationship_note == ""


def test_snapshot_is_immutable():
    snapshot = PersonaSnapshot(user_id="p1", version=2, source="user")
    with pytest.raises(ValidationError):
        snapshot.version = 3
