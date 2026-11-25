"""Person domain: user-confirmed people only, no NER auto-creation."""

from app.domain.person.service import (
    ALLOWED_EPISTEMIC,
    PersonRuleError,
    create_person,
    list_persons,
    name_norm,
    soft_delete_person,
    validate_epistemic,
)

__all__ = [
    "ALLOWED_EPISTEMIC",
    "PersonRuleError",
    "create_person",
    "list_persons",
    "name_norm",
    "soft_delete_person",
    "validate_epistemic",
]
