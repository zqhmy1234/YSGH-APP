"""B5-03 person rules: user-confirmed rows only, soft delete closes content links.

No AI inference and no automatic NER: a person exists only after an explicit user
or import action. The idempotency key is not a new column; the natural key
``(user_id, name_norm)`` is idempotent for the same display name, which is what a
retried create needs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.person import ContentPerson, Person
from app.domain.person.name_norm import RELATIONS, normalize_name, valid_relation
from app.services.query_helpers import alive_clause

# Backwards-compatible alias for callers that imported the old local helper.
name_norm = normalize_name

Epistemic = Literal["user_confirmed", "imported"]
ALLOWED_EPISTEMIC: frozenset[str] = frozenset({"user_confirmed", "imported"})


class PersonRuleError(ValueError):
    """A request would create an AI-inferred or invalid person."""


def validate_epistemic(epistemic: str) -> Epistemic:
    if epistemic not in ALLOWED_EPISTEMIC:
        raise PersonRuleError(f"epistemic must be user_confirmed|imported, got {epistemic!r}")
    return epistemic  # type: ignore[return-value]


def create_person(
    db: Session,
    *,
    user_id: str,
    display_name: str,
    idempotency_key: str,
    relation: str = "other",
    birth_date=None,
    notes: str = "",
    source: str = "user",
    epistemic: str = "user_confirmed",
) -> Person:
    """Create or return an existing same-name person; never infer a new one."""
    if not idempotency_key:
        raise PersonRuleError("idempotency_key is required")
    normalized = name_norm(display_name)
    if not normalized:
        raise PersonRuleError("display_name is required")
    validate_epistemic(epistemic)
    normalized_relation = (relation or "other").strip() or "other"
    if not valid_relation(normalized_relation):
        raise PersonRuleError(f"relation must be one of {sorted(RELATIONS)}")
    existing = db.execute(
        select(Person).where(
            Person.user_id == user_id,
            Person.name_norm == normalized,
            alive_clause(Person),
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Person(
        user_id=user_id,
        display_name=display_name.strip(),
        name_norm=normalized,
        relation=normalized_relation,
        birth_date=birth_date,
        notes=notes or "",
        source=source or "user",
        confidence=1.0,
        epistemic=epistemic,
    )
    db.add(row)
    db.flush()
    return row


def list_persons(db: Session, user_id: str, *, limit: int = 50) -> list[Person]:
    return list(
        db.execute(
            select(Person)
            .where(Person.user_id == user_id, alive_clause(Person))
            .order_by(Person.created_at.desc())
            .limit(max(1, min(limit, 100)))
        ).scalars()
    )


def soft_delete_person(db: Session, *, user_id: str, person_id: str) -> bool:
    """Soft-delete the person and close every active content relation."""
    row = db.execute(
        select(Person).where(Person.id == person_id, Person.user_id == user_id, alive_clause(Person))
    ).scalar_one_or_none()
    if row is None:
        return False
    now = datetime.now(UTC)
    row.deleted_at = now
    for link in db.execute(
        select(ContentPerson).where(ContentPerson.person_id == person_id, ContentPerson.valid_to.is_(None))
    ).scalars():
        link.valid_to = now
    db.flush()
    return True


__all__ = [
    "ALLOWED_EPISTEMIC",
    "PersonRuleError",
    "create_person",
    "list_persons",
    "name_norm",
    "soft_delete_person",
    "validate_epistemic",
]
