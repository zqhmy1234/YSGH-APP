"""L3 agent-facing endpoints: profile digest, persons, echo candidates.

Plan §3.3 / 45 §6.1. All endpoints use ``agent_service_user`` (shared service
token + X-On-Behalf-Of) and are excluded from the public OpenAPI schema.
"""

from __future__ import annotations

import logging

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
    MemoryWriteIn,
    MemoryWriteOut,
    PersonAgentCreate,
    PersonAgentOut,
)
from app.schemas.common import ApiResponse
from app.services.echo import peek_today_echo

logger = logging.getLogger(__name__)

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
    from app.services.consent import is_granted
    from app.services.profile_digest import build_agent_digest

    # B6 用途授权：画像投影属 personalization 用途，未授权时不给 Agent 画像内容。
    # 默认不勾 ⇒ 存量用户升级后 Agent 暂时拿不到画像，须在设置里主动开启（产品拍板 2026-11-24）。
    if not is_granted(db, str(user.id), "personalization"):
        return ApiResponse(data=AgentDigestOut(
            status="unavailable",
            text="用户未授权个性化画像用途",
            chars=0,
            sections=[],
        ))

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
    # B6 用途授权：人物关系属 personalization 用途，未授权不得新建（与 list/digest 同闸）
    from app.services.consent import is_granted
    if not is_granted(db, str(user.id), "personalization"):
        raise HTTPException(status_code=403, detail={"code": "CONSENT_REQUIRED",
                                                      "message": "未授权个性化画像用途"})
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
    # B5-03 收口：用户确认 person 后，把显式提到该人名字的历史内容关联到
    # content_persons（确定性子串，不新建 person）。
    # 关键：回填必须放在 create_person 的 flush **之后**、用 savepoint 隔离——
    # 若回填失败直接 db.rollback() 会把同事务里刚 flush 的 Person 一起撤销（审判 P0）。
    try:
        from app.domain.person.backfill import backfill_person_contents

        with db.begin_nested():
            backfill_person_contents(db, str(user.id), row)
    except Exception:  # noqa: BLE001 —— 回填失败只回滚 savepoint，Person 保留
        logger.warning("B5-03 person backfill failed for person=%s", row.id, exc_info=True)
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
                # peek_today_echo 返回 {content_id, text, taken_at, place, echo_date, fingerprint}，
                # 无 title/subtitle 字段（2026-11-24 审判修正：原 get("title"/"subtitle") 恒走 fallback）
                title=str(result.get("text") or "去年今天"),
                reason="anniversary",
                sensitivity="personal",
                score=0.8,
                why_now=str(result.get("echo_date") or "去年今天"),
            )
        )
    items = items[: req.limit]
    return ApiResponse(data=EchoAgentResponse(items=items, suppressed="" if items else "no_candidate"))


@router.post(
    "/memory/agent",
    response_model=ApiResponse[MemoryWriteOut],
    include_in_schema=False,
)
def write_memory_for_agent(
    req: MemoryWriteIn,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """B5 合并：Agent 对话记忆写入 canonical 账本（替代旧的 V3 大 JSON 自写）。

    entry_id 由 agent 侧 ledger 生成（m_<12hex>），服务端原样采用——这样 agent
    的幂等键与 canonical 主键一致，重试不会写两条。

    **写入默认开、无 personalization 闸**：记用户自己的事是基础功能（consent.py
    模块契约明示「基础功能不是授权项」）。授权闸只管额外用途——agent 拿记忆做
    个性化回复（GET /memory/agent）仍受 personalization 约束。
    """
    from datetime import UTC, datetime

    from app.db.models.memory import MemoryEntry
    from app.services.audit import record_audit

    existing = db.get(MemoryEntry, req.entry_id)
    if existing is not None:
        return ApiResponse(data=MemoryWriteOut(entry_id=req.entry_id, idempotent=True))

    valid = [e for e in req.evidence if isinstance(e, dict)
             and str(e.get("type") or "").strip() and str(e.get("id") or "").strip()]
    if not valid:
        return ApiResponse(data=MemoryWriteOut(
            entry_id=req.entry_id, status="rejected", reason="missing_evidence"))

    row = MemoryEntry(
        entry_id=req.entry_id,
        user_id=user.id,
        layer=req.layer,
        kind=req.kind,
        text=req.text,
        summary=req.summary,
        story_arc=req.story_arc,
        valid_from=req.valid_from or datetime.now(UTC).date(),
        evidence=valid,
        confidence=req.confidence,
        sensitivity=req.sensitivity,
        created_by="agent_merge",
    )
    try:
        db.add(row)
        record_audit(db, str(user.id), action="memory_written", target=req.entry_id)
        db.commit()
    except Exception:  # noqa: BLE001 —— 写入失败必须整体回滚，不留无审计的记忆
        db.rollback()
        raise
    return ApiResponse(data=MemoryWriteOut(entry_id=req.entry_id))


@router.delete(
    "/memory/agent/{entry_id}",
    response_model=ApiResponse[dict],
    include_in_schema=False,
)
def delete_memory_for_agent(
    entry_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(agent_service_user),
):
    """B5 合并：Agent purge 回写 canonical 账本，避免下次 hydrate 复活。

    删除用户自己的记忆与写入同属基础功能，默认开、无授权闸。
    """
    from app.db.models.memory import MemoryEntry

    row = db.get(MemoryEntry, entry_id)
    if row is None or str(row.user_id) != str(user.id):
        return ApiResponse(data={"entry_id": entry_id, "deleted": False})
    db.delete(row)
    db.commit()
    return ApiResponse(data={"entry_id": entry_id, "deleted": True})


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
    # B6 用途授权：人物关系属 personalization 用途；未授权不返回任何 person。
    from app.services.consent import is_granted
    if not is_granted(db, str(user.id), "personalization"):
        return ApiResponse(data=[])
    rows = list_persons(db, user.id, limit=limit)
    # person_id 查询参数此前声明却未使用（审判 nail）：传入时只回该人。
    if person_id:
        rows = [r for r in rows if str(r.id) == person_id or r.display_name == person_id]
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
