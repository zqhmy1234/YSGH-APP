"""Echo domain: "what came back today", with six gates before anything surfaces.

Gates (all must pass, in this order):
  1. user opted in;
  2. not quiet hours;
  3. role may see the sensitivity;
  4. at least two episodes support the echo (no single-fluke memories);
  5. cooldown for the same echo key (default 30 days);
  6. a concrete "why now" (anniversary / place / person / season).

A failed gate returns nothing. It never returns a lower-scored guess.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date

from harness.contracts import EchoCandidate, Episode, FamilyRole

from .trust import TrustGate

COOLDOWN_DAYS = 30


class EchoPolicy:
    def __init__(self, gate: TrustGate | None = None, cooldown_days: int = COOLDOWN_DAYS) -> None:
        self._gate = gate or TrustGate()
        self._cooldown = cooldown_days

    def candidates(
        self,
        person_id: str,
        episodes: Sequence[Episode],
        today: date,
        *,
        role: FamilyRole = FamilyRole.ADULT,
        opted_in: bool = True,
        quiet_hours: bool = False,
        last_sent: Mapping[str, date] | None = None,
    ) -> list[EchoCandidate]:
        if not opted_in or quiet_hours:
            return []
        last_sent = dict(last_sent or {})
        out: list[EchoCandidate] = []

        for key, group in self._groups(episodes).items():
            if len(group) < 2:
                continue
            sensitivity = max((e.sensitivity for e in group), key=_severity)
            if not self._gate.visible(role, sensitivity):
                continue
            last = last_sent.get(key)
            if last is not None and (today - last).days < self._cooldown:
                continue
            reason, why = _why(key, group, today)
            if reason is None:
                continue
            score = min(0.95, 0.6 + 0.05 * len(group))
            out.append(
                EchoCandidate(
                    echo_id=f"echo_{abs(hash(key)) % 10**8}",
                    person_id=person_id,
                    title=group[0].text[:30],
                    reason=reason,
                    sensitivity=sensitivity,
                    score=round(score, 2),
                    why_now=why,
                )
            )
        return sorted(out, key=lambda c: c.score, reverse=True)

    @staticmethod
    def _groups(episodes: Sequence[Episode]) -> dict[str, list[Episode]]:
        groups: dict[str, list[Episode]] = defaultdict(list)
        for ep in episodes:
            if ep.place:
                groups[f"place:{ep.place}"].append(ep)
            for person in ep.people:
                groups[f"person:{person}"].append(ep)
            groups[f"day:{ep.day.month}-{ep.day.day}"].append(ep)
        return groups


def _why(key: str, group: Sequence[Episode], today: date):
    kind, _, value = key.partition(":")
    if kind == "day":
        month, _, day = value.partition("-")
        if int(month) == today.month and int(day) == today.day:
            return "anniversary", f"{today.year} 年的今天"
        return None, ""
    if kind == "place":
        return "place", f"你在这个地方留下了 {len(group)} 段记忆"
    if kind == "person":
        return "person", f"和 {value} 有关的 {len(group)} 段记忆"
    return None, ""


def _severity(sensitivity) -> int:
    order = {"public": 0, "personal": 1, "sensitive": 2, "critical": 3}
    return order.get(getattr(sensitivity, "value", str(sensitivity)), 0)
