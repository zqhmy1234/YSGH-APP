"""B7-03 probes: no confidence, not_measured stays invisible."""

from __future__ import annotations

import pytest
from app.schemas.emotion import EmotionContract
from pydantic import ValidationError


def test_schema_has_no_confidence_field():
    assert "confidence" not in EmotionContract.model_fields
    assert set(EmotionContract.model_fields) == {
        "state", "valence", "arousal", "cues", "basis", "display"
    }


def test_not_measured_never_looks_like_calm():
    contract = EmotionContract(state="not_measured")
    assert contract.valence is None and contract.arousal is None
    assert contract.visible() is False


def test_display_off_hides_even_measured_emotion():
    contract = EmotionContract(
        state="measured", valence=-0.4, arousal=0.7, cues=("语速变慢",), basis="语音线索", display="off"
    )
    assert contract.visible() is False


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        EmotionContract(state="measured", confidence=0.9)


def test_valence_is_bounded():
    with pytest.raises(ValidationError):
        EmotionContract(state="measured", valence=2.0)

def test_asr_public_response_has_no_emotion_confidence():
    """B7-03 / 决策 29.10：情绪置信度只用于内部阈值，不进对外契约。"""
    from app.schemas.asr import AsrTranscribeResponse

    assert "emotion_confidence" not in AsrTranscribeResponse.model_fields
