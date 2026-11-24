"""B5-02 read model: four B5 layers projected into one ``MemorySnapshot``.

This module is the only place that turns ORM rows into the agent-facing snapshot.
It never writes. ``ok`` means at least one real layer has content;
``insufficient_data`` means the account exists but has nothing yet; ``unavailable``
is reserved for a store failure (the API catches exceptions and returns it).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models.content import Content
from app.db.models.memory import MemoryCorrection, MemoryEntry
from app.db.models.profile import UserProfile
from app.ports.memory import Correction, MemorySnapshot, NarrativeEpisode
from app.services.profile_digest import build_agent_digest
from app.services.query_helpers import alive_clause

NARRATIVE_LIMIT = 5
CORRECTION_LIMIT = 10
ASSET_LIMIT = 8


@dataclass(frozen=True)
class RawMemory:
    narrative: tuple[NarrativeEpisode, ...] = ()
    corrections: tuple[Correction, ...] = ()
    digest: dict | None = None
    assets: tuple[dict, ...] = ()
    note: str = ""


def _episode(row: Any) -> NarrativeEpisode:
    evidence = tuple(
        str(item.get("id") or item.get("content_id") or "")
        for item in (row.evidence or ())
        if isinstance(item, dict) and (item.get("id") or item.get("content_id"))
    )
    valid_from = row.valid_from.isoformat() if isinstance(row.valid_from, date) else str(row.valid_from or "")
    return NarrativeEpisode(
        entry_id=row.entry_id,
        text=row.text,
        evidence=evidence,
        valid_from=valid_from,
        story_arc=row.story_arc or "",
    )


def build_snapshot(user_id: str, raw: RawMemory) -> MemorySnapshot:
    has_real_layer = bool(raw.narrative or raw.corrections or raw.assets)
    digest_ok = bool(raw.digest and raw.digest.get("status") == "ok")
    status = "ok" if (has_real_layer or digest_ok) else "insufficient_data"
    return MemorySnapshot(
        user_id=user_id,
        status=status,
        narrative=raw.narrative,
        corrections=raw.corrections,
        digest=raw.digest,
        asset_index=raw.assets,
        note=raw.note,
    )


def unavailable_snapshot(user_id: str, note: str = "store_unavailable") -> MemorySnapshot:
    return MemorySnapshot(user_id=user_id, status="unavailable", note=note)


def read_snapshot(db: Session, user_id: str) -> MemorySnapshot:
    profile = db.execute(select(UserProfile).where(UserProfile.user_id == user_id)).scalar_one_or_none()
    digest = build_agent_digest(profile.dimensions if profile else None)
    digest_ok = digest.status == "ok"  # 供下方 note 判定：digest 本身即一层真实记忆
    digest_payload = {
        "status": digest.status,
        "text": digest.text,
        "chars": digest.chars,
        "sections": [{"name": s.name, "text": s.text} for s in digest.sections],
    }

    entries = list(
        db.execute(
            select(MemoryEntry)
            .where(
                MemoryEntry.user_id == user_id,
                MemoryEntry.layer == "narrative",
                MemoryEntry.valid_to.is_(None),
                MemoryEntry.sensitivity != "forbidden",
            )
            .order_by(MemoryEntry.recorded_at.desc())
            .limit(NARRATIVE_LIMIT)
        ).scalars()
    )
    corrections = list(
        db.execute(
            select(MemoryCorrection)
            .where(MemoryCorrection.user_id == user_id)
            .order_by(MemoryCorrection.created_at.desc())
            .limit(CORRECTION_LIMIT)
        ).scalars()
    )
    # B6 保险箱：锁住的内容不进记忆快照 assets（/memory/agent 与本地读模型共用）。
    # 必须先过滤再 LIMIT——否则最新 8 条全锁会让第 9/10 条可见内容永远拿不到
    # （审判 nail：明明有可见记忆却对 agent 谎称 insufficient_data）。
    try:
        from app.services.vault import locked_content_ids
        vaulted = locked_content_ids(db, user_id)
    except Exception:  # noqa: BLE001 —— 保险箱读失败 fail-closed，不泄露任何原文
        vaulted = None
    assets_q = (
        select(Content)
        .where(Content.user_id == user_id, alive_clause(Content), Content.text.is_not(None))
        .order_by(Content.created_at.desc())
    )
    if vaulted:
        assets_q = assets_q.where(Content.id.notin_(vaulted))
    elif vaulted is None:
        assets_q = assets_q.where(False)  # 读保险箱失败 ⇒ 零 assets，fail-closed
    assets = list(db.execute(assets_q.limit(ASSET_LIMIT)).scalars())

    raw = RawMemory(
        narrative=tuple(_episode(row) for row in entries),
        corrections=tuple(
            Correction(target_type=row.target_type, target_id=row.target_id, field=row.field, value=row.value)
            for row in corrections
        ),
        digest=digest_payload,
        assets=tuple(
            {
                "content_id": row.id,
                "text": (row.text or "")[:200],
                "taken_at": row.taken_at.isoformat() if isinstance(row.taken_at, datetime) else "",
            }
            for row in assets
            if row.text
        ),
        # digest 本身就是一层真实记忆：digest=ok 时不能同时声称 empty_account
        # （审判 nail：口径自相矛盾会让 agent 误判用户没有任何记忆）
        note="empty_account" if not entries and not corrections and not assets and not digest_ok else "",
    )
    return build_snapshot(user_id, raw)


__all__ = ["RawMemory", "build_snapshot", "read_snapshot", "unavailable_snapshot"]
