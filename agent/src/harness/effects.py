"""Side effects after a verified turn: echo scheduling, audit, persona proposals.

Effects are explicit plan entries, idempotent by key, and never run before
VERIFY. ``EchoEffects`` is a pure planner; persistence is an injected callable
owned by the composition root.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date

from harness.contracts import EchoCandidate, TurnPlan
from harness.domain.echo import EchoPolicy


@dataclass
class EffectReceipt:
    kind: str
    applied: bool
    idempotency_key: str = ""
    reason: str = ""


class EchoEffects:
    def __init__(self, policy: EchoPolicy | None = None) -> None:
        self._policy = policy or EchoPolicy()

    def plan(
        self,
        *,
        person_id: str,
        episodes: list,
        today: date,
        opted_in: bool = True,
        quiet_hours: bool = False,
        last_sent: Mapping[str, date] | None = None,
    ) -> list[EchoCandidate]:
        if not opted_in or quiet_hours:
            return []
        return self._policy.candidates(
            person_id, episodes, today, opted_in=opted_in, quiet_hours=quiet_hours, last_sent=last_sent
        )

    def apply(
        self,
        plan: TurnPlan,
        *,
        schedule: Callable[[str, str], bool],
        cancel: Callable[[str], bool],
        audit: Callable[[str, dict], None],
        person_id: str,
        payload: dict | None = None,
    ) -> tuple[EffectReceipt, ...]:
        receipts: list[EffectReceipt] = []
        for effect in plan.side_effects:
            key = effect.idempotency_key or f"{effect.kind}:{person_id}"
            try:
                if effect.kind == "schedule_echo":
                    receipts.append(EffectReceipt(effect.kind, bool(schedule(person_id, key)), key))
                elif effect.kind == "cancel_echo":
                    receipts.append(EffectReceipt(effect.kind, bool(cancel(key)), key))
                elif effect.kind == "audit":
                    audit(person_id, dict(payload or {}))
                    receipts.append(EffectReceipt(effect.kind, True, key))
                else:
                    # persona changes are proposals handled by the memory lane
                    receipts.append(EffectReceipt(effect.kind, False, key, "not_committed_here"))
            except Exception as exc:  # noqa: BLE001 - effects must never break the reply
                receipts.append(EffectReceipt(effect.kind, False, key, f"failed:{type(exc).__name__}"))
        return tuple(receipts)


__all__ = ["EchoEffects", "EffectReceipt"]
