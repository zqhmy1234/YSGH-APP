"""B5-02 probes: wire snapshot parsing fails honest, not empty."""

from __future__ import annotations

from memory.read_model import parse_snapshot


def test_valid_payload_parses_three_layers():
    snapshot = parse_snapshot(
        {
            "user_id": "u1",
            "status": "ok",
            "narrative": [{"entry_id": "m1", "text": "西湖", "evidence": ["c1"], "valid_from": "2026-01-01"}],
            "corrections": [{"target_type": "entry", "target_id": "m1", "field": "text", "value": "新"}],
            "digest": {"status": "ok", "text": "身份：教师"},
            "asset_index": [{"content_id": "c1"}],
        },
        expected_user="u1",
    )
    assert snapshot.status == "ok"
    assert snapshot.narrative[0].entry_id == "m1"
    assert snapshot.corrections[0].value == "新"
    assert snapshot.asset_index[0]["content_id"] == "c1"


def test_malformed_status_is_unavailable_with_note():
    snapshot = parse_snapshot({"user_id": "u1", "status": "weird"}, expected_user="u1")
    assert snapshot.status == "unavailable"
    assert "contract_drift" in snapshot.note


def test_user_mismatch_is_not_silently_accepted():
    snapshot = parse_snapshot({"user_id": "u2", "status": "ok"}, expected_user="u1")
    assert snapshot.status == "unavailable"
    assert snapshot.note == "user_mismatch"
