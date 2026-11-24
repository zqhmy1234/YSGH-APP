"""Conflict precedence matches the five rules in design §5.3."""

from app.domain.memory.conflict import Candidate, resolve


def test_user_correction_always_wins():
    winner, rows = resolve(
        [
            Candidate(value="栀子花", source="correction", recorded_at=1),
            Candidate(value="桂花", source="confirmed_fact", recorded_at=99),
        ]
    )
    assert winner == "correction"
    assert rows[0].value == "栀子花"


def test_two_confirmed_with_different_dates_use_latest():
    winner, rows = resolve(
        [
            Candidate(value="旧地址", source="confirmed_fact", recorded_at=1),
            Candidate(value="新地址", source="confirmed_fact", recorded_at=2),
        ]
    )
    assert winner == "latest" and rows[0].value == "新地址"


def test_indistinguishable_confirmed_values_are_pending_not_guessed():
    winner, rows = resolve(
        [
            Candidate(value="A", source="confirmed_fact", recorded_at=5),
            Candidate(value="B", source="confirmed_fact", recorded_at=5),
        ]
    )
    assert winner == "pending_confirmation" and len(rows) == 2


def test_inference_never_silently_beats_confirmed_fact():
    winner, _ = resolve(
        [
            Candidate(value="模型猜", source="inference", confidence=0.99),
            Candidate(value="用户确认", source="confirmed_fact", recorded_at=1),
        ]
    )
    assert winner == "confirmed_fact"
