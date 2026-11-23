"""B5-02 internal memory read model endpoint.

Only the agent service token can call it. The endpoint never writes and never
returns ORM objects; ``unavailable`` is a 200 with an honest note, so the agent
can degrade instead of treating an empty list as "no memories".
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import Depends
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import agent_service_user
from app.db.models import User
from app.db.session import get_db
from app.domain.memory import read_snapshot, unavailable_snapshot
from app.schemas.common import ApiResponse

router = make_router(prefix="/api/v1/memory", tags=["agent-internal"])


def _payload(snapshot) -> dict:
    data = asdict(snapshot)
    # JSON-safe tuples for FastAPI; dataclasses.asdict keeps nested tuples.
    data["narrative"] = [
        {
            "entry_id": item["entry_id"],
            "text": item["text"],
            "evidence": list(item["evidence"]),
            "valid_from": item["valid_from"],
            "story_arc": item["story_arc"],
        }
        for item in data["narrative"]
    ]
    data["corrections"] = [dict(item) for item in data["corrections"]]
    data["asset_index"] = [dict(item) for item in data["asset_index"]]
    return data


@router.get("/agent", response_model=ApiResponse[dict], include_in_schema=False)
def get_memory_for_agent(db: Session = Depends(get_db), user: User = Depends(agent_service_user)):
    """One snapshot per turn, three honest states; no user id in the query."""
    try:
        snapshot = read_snapshot(db, user.id)
    except Exception as exc:  # noqa: BLE001 —— storage failure must be visible, not an empty list
        snapshot = unavailable_snapshot(user.id, note=f"store_error:{type(exc).__name__}")
    return ApiResponse(data=_payload(snapshot))


__all__ = ["router"]
