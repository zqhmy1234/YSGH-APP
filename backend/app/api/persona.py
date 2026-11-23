"""B7-01: user-owned relationship choices.

``PUT`` is the only write path; the agent reads one snapshot per turn. There is no
process-global prompt mutation: ``ContextAssembler`` receives the snapshot and
renders the full relationship paragraph fresh.
"""

from __future__ import annotations

from fastapi import Body, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import agent_service_user, get_current_user
from app.db.models import User, UserAIPersona
from app.db.session import get_db
from app.domain.persona import (
    RELATIONSHIP_QUESTIONS,
    ROLE_CARD_OPTIONS,
    PersonaSnapshot,
    PersonaUpdate,
    apply_update,
    relationship_note,
    snapshot_from_row,
)
from app.schemas.common import ApiResponse
from app.schemas.persona import (
    PersonaChoiceQuestionOut,
    PersonaOptionsOut,
    PersonaOut,
    PersonaUpdateIn,
    RoleCardOptionOut,
)

router = make_router(prefix="/api/v1/persona", tags=["persona"])
_ALLOWED_ROLE_IDS = {item["role_id"] for item in ROLE_CARD_OPTIONS}


def _out(snapshot: PersonaSnapshot) -> PersonaOut:
    return PersonaOut(
        user_id=snapshot.user_id,
        choices=snapshot.choices,
        role_id=snapshot.role_id,
        version=snapshot.version,
        updated_at=snapshot.updated_at,
        source=snapshot.source,
        relationship_note=relationship_note(snapshot.choices),
    )


def _load(db: Session, user_id: str) -> PersonaSnapshot:
    row = db.execute(select(UserAIPersona).where(UserAIPersona.user_id == user_id)).scalar_one_or_none()
    return snapshot_from_row(user_id, row)


@router.get("/options", response_model=ApiResponse[PersonaOptionsOut])
def get_persona_options():
    """Full sentences, not internal labels: the client renders them verbatim."""
    return ApiResponse(
        data=PersonaOptionsOut(
            questions=tuple(
                PersonaChoiceQuestionOut(question=item["question"], options=item["options"])
                for item in RELATIONSHIP_QUESTIONS
            ),
            role_cards=tuple(RoleCardOptionOut(**item) for item in ROLE_CARD_OPTIONS),
        )
    )


@router.get("", response_model=ApiResponse[PersonaOut])
def get_persona(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Missing row is a default snapshot with ``source=default``, never 404."""
    return ApiResponse(data=_out(_load(db, user.id)))


@router.put("", response_model=ApiResponse[PersonaOut])
def put_persona(
    payload: PersonaUpdateIn = Body(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Validate three choices and the selected person; one version bump."""
    if payload.role_id not in _ALLOWED_ROLE_IDS:
        raise HTTPException(status_code=400, detail="unknown role_id")
    row = db.get(UserAIPersona, user.id)
    if row is None:
        row = UserAIPersona(user_id=user.id)
        db.add(row)
    update = PersonaUpdate(role_id=payload.role_id, **payload.choices.model_dump())
    apply_update(row, update, role_id=payload.role_id)
    db.commit()
    db.refresh(row)
    return ApiResponse(data=_out(snapshot_from_row(user.id, row)))


@router.get("/agent", response_model=ApiResponse[PersonaOut], include_in_schema=False)
def get_persona_for_agent(db: Session = Depends(get_db), user: User = Depends(agent_service_user)):
    """L3 read for the agent; the shared service token selects the on-behalf user."""
    return ApiResponse(data=_out(_load(db, user.id)))


__all__ = ["router"]
