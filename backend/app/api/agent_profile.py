"""L3 agent-facing endpoints: profile digest, persons, echo candidates.

Plan §3.3 / 45 §6.1. All endpoints use ``agent_service_user`` (shared service
token + X-On-Behalf-Of) and are excluded from the public OpenAPI schema.
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import agent_service_user
from app.db.models import User, UserProfile
from app.db.session import get_db
from app.domain.person import PersonRuleError, create_person, list_persons
from app.schemas.agent_digest import (
    AgentDigestOut,
    AgentDigestSection,
    EchoAgentCandidate,
    EchoAgentRequest,
    EchoAgentResponse,
    PersonAgentCreate,
    PersonAgentOut,
)
from app.schemas.common import ApiResponse
from app.services.echo import peek_today_echo

router = make_router(prefix="/api/v1", tags=["agent-internal"])


def _person_out(row) -> PersonAgentOut:
    return PersonAgentOut(
        person_id=row.id,
        display_name=row.display_name,
        relation=row.relation,
        birth_date=row.birth_date,
        source=row.source,
        confidence=float(row.confidence or 1.0),
    )


@router.get(
    "/profile/agent/digest",
    response_model=ApiResponse[AgentDigestOut],
    include_in_schema=False,
)
def agent_profile_digest(
    person_id: str = Query(..., min_length=1, max_length=64),
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """Return the 8-section projection in three honest states.

    ``insufficient_data`` and ``unavailable`` are 200 responses with an explicit
    status; only a missing service token is an error. This endpoint must never
    answer 4xx just because the user has no profile yet (45 §6.1).
    """
    from app.services.profile_digest import build_agent_digest

    profile = db.execute(select(UserProfile).where(UserProfile.user_id == user.id)).scalar_one_or_none()
    digest = build_agent_digest(profile.dimensions if profile else None)
    return ApiResponse(
        data=AgentDigestOut(
            status=digest.status,
            text=digest.text,
            chars=digest.chars,
            sections=[AgentDigestSection(name=s.name, text=s.text) for s in digest.sections],
        )
    )


@router.post(
    "/persons/agent",
    response_model=ApiResponse[PersonAgentOut],
    include_in_schema=False,
)
def create_person_for_agent(
    req: PersonAgentCreate,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """Explicitly user-confirmed person creation.

    B5-03 forbids AI-inferred rows: the service accepts only
    ``user_confirmed|imported`` and never parses ``memories.people`` here.
    """
    try:
        row = create_person(
            db,
            user_id=user.id,
            display_name=req.display_name,
            idempotency_key=req.idempotency_key,
            relation=req.relation,
            birth_date=req.birth_date,
            notes=req.notes,
            epistemic="user_confirmed",
        )
    except PersonRuleError as exc:
        raise HTTPException(status_code=422, detail={"code": "PERSON_INVALID", "message": str(exc)}) from exc
    db.commit()
    db.refresh(row)
    return ApiResponse(data=_person_out(row))


@router.post(
    "/echo/agent",
    response_model=ApiResponse[EchoAgentResponse],
    include_in_schema=False,
)
def echo_for_agent(
    req: EchoAgentRequest,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """Read-only echo candidates for the agent's ECHO intent.

    Reuses the same selection rules as ``get_today_echo`` but via
    ``peek_today_echo``: no EchoHistory row and no message is written here.
    """
    result = peek_today_echo(db, user.id)
    items: list[EchoAgentCandidate] = []
    if result:
        items.append(
            EchoAgentCandidate(
                echo_id=str(result.get("fingerprint") or result.get("content_id") or "echo"),
                person_id=req.person_id,
                title=str(result.get("title") or result.get("text") or "去年今天"),
                reason="anniversary",
                sensitivity="personal",
                score=0.8,
                why_now=str(result.get("subtitle") or "去年今天"),
            )
        )
    items = items[: req.limit]
    return ApiResponse(data=EchoAgentResponse(items=items, suppressed="" if items else "no_candidate"))


@router.get(
    "/persons/agent",
    response_model=ApiResponse[list[PersonAgentOut]],
    include_in_schema=False,
)
def list_persons_for_agent(
    person_id: str = Query("", max_length=64),
    limit: int = Query(50, ge=1, le=100),
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """List only live, user-confirmed persons for the on-behalf account."""
    rows = list_persons(db, user.id, limit=limit)
    return ApiResponse(data=[_person_out(row) for row in rows])


@router.delete(
    "/persons/agent/{person_id}",
    response_model=ApiResponse[dict],
    include_in_schema=False,
)
def delete_person_for_agent(
    person_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """Soft-delete and close content links; unknown/already deleted is 404."""
    from app.domain.person import soft_delete_person

    if not soft_delete_person(db, user_id=user.id, person_id=person_id):
        raise HTTPException(status_code=404, detail={"code": "PERSON_NOT_FOUND", "message": "person not found"})
    db.commit()
    return ApiResponse(data={"person_id": person_id, "deleted": True})
