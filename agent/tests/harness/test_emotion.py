"""EmotionContract: no confidence, not_measured is not calm, user correction wins."""

from __future__ import annotations

from harness.emotion import EmotionContract, EmotionCorrection


def test_contract_has_no_confidence_field():
    assert "confidence" not in EmotionContract.model_fields


def test_not_measured_is_not_rendered_as_calm():
    rendered = EmotionContract(state="not_measured", valence=0.0, arousal=0.0).rendered()
    assert rendered == {"state": "not_measured"}


def test_measured_has_explicit_values_and_basis():
    rendered = EmotionContract(
        state="measured", valence=-0.6, arousal=0.4, cues=("叹气",), basis="用户原话：累"
    ).rendered()
    assert rendered["valence"] == -0.6 and rendered["basis"]


def test_correction_applies_next_turn():
    correction = EmotionCorrection(user_value="不是难过，是累")
    assert correction.apply_next_turn
