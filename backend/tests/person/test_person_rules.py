"""B5-03 probes: no AI-inferred persons, stable name normalization."""

from __future__ import annotations

import pytest
from app.domain.person import PersonRuleError, name_norm, validate_epistemic


def test_name_norm_is_case_punct_and_space_insensitive():
    assert name_norm("  Lin   Xiao ") == "linxiao"
    assert name_norm("林·小  白") == "林小白"


def test_only_user_confirmed_or_imported_is_accepted():
    assert validate_epistemic("user_confirmed") == "user_confirmed"
    assert validate_epistemic("imported") == "imported"
    with pytest.raises(PersonRuleError):
        validate_epistemic("ai_inferred")
