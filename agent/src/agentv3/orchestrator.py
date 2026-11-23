"""Orchestrator: the composition root of one turn.

It is the only module that knows the order of agents. Each agent stays unaware of
the others: they receive contracts and return contracts, so swapping an agent or
running one alone in a test never requires touching this file beyond one line.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Mapping, Sequence

from .agents.librarian import Librarian
from .agents.muse import Muse
from .agents.provenance import Provenance
from .agents.sculptor import Sculptor
from .contracts import (
    Claim,
    ClaimBundle,
    Cost,
    Frame,
    GuardAction,
    GuardRequest,
    Intent,
    MemoryLayer,
    MemoryProposal,
    OpKind,
    ResultStatus,
    SearchResult,
    TurnContext,
    TurnRequest,
    TurnResult,
    Verdict,
    VerdictStatus,
    WriteReceipt,
)
from .frame import build_frame
from .memory.ledger import MemoryStore
from .memory.policy import Policy
from .memory.projection import build as build_digest
from .obs import CostMeter, Degradation
from .plan import plan

DEGRADED_REPLY = "我这会儿有点乱，但还在。你能把刚才那句话再说一遍吗？我慢慢接。"


class Orchestrator:
    def __init__(
        self,
        *,
        llm,
        search,
        events,
        store: MemoryStore,
        policy: Policy | None = None,
        clock=None,
        persona: str = "你是忆述光华的陪伴者：温和、具体、不编造，不替用户下结论。",
        relation: str = "关系：长期陪伴的朋友，认识很久。",
        tools: Mapping[str, object] | None = None,
        tool_schemas: Sequence[dict] = (),
        system_prefix: str = "",
    ) -> None:
        self._llm = llm
        self._store = store
        self._policy = policy or Policy()
        self._clock = clock
        self._persona = persona
        self._relation = relation
        self._librarian = Librarian(search, events)
        self._muse = Muse(llm, tools, system_prefix=system_prefix, tool_schemas=tuple(tool_schemas))  # type: ignore[arg-type]
        self._sculptor = Sculptor(store)

    # -- helpers ------------------------------------------------------
    def set_system_prefix(self, text: str) -> None:
        self._muse.set_system_prefix(text)
    def _now(self) -> datetime:
        return self._clock.now() if self._clock else datetime.now()

    def _claims(self, reply: str, labels: Mapping[str, str] | None = None) -> tuple[Claim, ...]:
        labels = labels or {}
        sentences = self._policy.sentences(reply)
        claims: list[Claim] = []
        for sentence in sentences:
            kind = "plain"
            if any(ch.isdigit() for ch in sentence) or "次" in sentence or "岁" in sentence:
                kind = "numeric"
            elif any(word in sentence for word in ("年", "月", "日", "去年", "上次")):
                kind = "date"
            elif any(word in sentence for word in ("她", "他", "住在", "叫", "喜欢", "养", "上班", "结婚", "搬", "生日")):
                kind = "private"
            cites = tuple(labels.get(part, part) for part in Muse.cites(sentence))
            claims.append(Claim(text=sentence, cites=cites, kind=kind))
        return tuple(claims)

    def _citations(self, reply: str, labels: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(c for c in Muse.cites(reply) if c in labels)

    # -- turn ---------------------------------------------------------
    def run(self, request: TurnRequest, *, degraded: Sequence[str] = ()) -> TurnResult:
        started = time.monotonic()
        ctx = TurnContext(request=request, now=self._now(), degraded=tuple(degraded), hour=self._now().hour)
        turn_plan = plan(ctx)

        if turn_plan.fallback == "refuse" or turn_plan.intent is Intent.OUT_OF_SCOPE:
            return TurnResult(
                reply="这个问题不在我能陪你聊的范围里；回忆、情绪和日常我都在。",
                status=ResultStatus.GROUNDED,
                plan=turn_plan,
                cost=Cost(ms=int((time.monotonic() - started) * 1000)),
            )

        guard = self._policy.screen_input(GuardRequest(person_id=request.person_id, text=request.text, intent=turn_plan.intent))
        if guard.action is GuardAction.HANDOFF:
            return TurnResult(
                reply=guard.reply or DEGRADED_REPLY,
                status=ResultStatus.GROUNDED,
                plan=turn_plan,
                cost=Cost(ms=int((time.monotonic() - started) * 1000)),
            )

        degradation = Degradation()
        meter = CostMeter()
        # A caller-declared LLM outage means we must not even try the network;
        # the smoke/roleplay harness uses this to prove the fallback path.
        llm_down = "llm" in ctx.degraded

        try:
            entries = self._store.entries(request.person_id)
            corrections = tuple(row["value"] for row in self._store.corrections(request.person_id) if row["field"] == "text")
        except Exception:
            entries, corrections = None, ()
            degradation.note("memory")

        digest = build_digest(entries, request.person_id)

        evidence: SearchResult | None = None
        if any(inv.name.value == "librarian" for inv in turn_plan.invocations):
            query = self._librarian.resolve_query(request.text, request.person_id)
            try:
                evidence = self._librarian.retrieve(query, degraded=ctx.degraded)
                if evidence.degraded:
                    degradation.note("search")
            except Exception:
                evidence = None
                degradation.note("search")

        frame: Frame = build_frame(
            plan=turn_plan,
            persona=self._persona,
            relation=self._relation,
            boundaries=list(self._policy.boundaries),
            corrections=list(corrections),
            digest=digest,
            narrative="",
            evidence=evidence,
        )

        reply = ""
        misses: list[str] = []
        if llm_down:
            misses.append("llm")
            degradation.note("llm")
        else:
            try:
                reply, _ = self._muse.speak(frame, user_text=request.text, locale=request.locale, meter=meter)
            except Exception:
                misses.append("llm")
                degradation.note("llm")
        if not reply:
            reply = DEGRADED_REPLY

        evidence_ids = evidence.evidence_ids if evidence else ()
        label_to_id = {h.cite_as: h.evidence_id for h in (*evidence.summaries, *evidence.hits)} if evidence else {}
        bundle = ClaimBundle(claims=self._claims(reply, label_to_id), evidence=evidence_ids, crisis=False)
        provenance = Provenance(self._policy, corrections)
        verdict: Verdict = provenance.verify(bundle, evidence_ids)

        receipts: list[WriteReceipt] = []
        if turn_plan.write_policy == "propose" and not misses:
            proposal = MemoryProposal(
                op=OpKind.ADD,
                layer=MemoryLayer.NARRATIVE,
                text=request.text.strip()[:400],
                evidence=evidence_ids[:3],
                reason="turn_capture",
            )
            receipts.append(self._store.propose(proposal, person_id=request.person_id))

        status = ResultStatus.GROUNDED
        if misses:
            status = ResultStatus.PARTIAL
        if verdict.status is VerdictStatus.HANDOFF:
            reply = DEGRADED_REPLY
            status = ResultStatus.PARTIAL
        elif verdict.status is VerdictStatus.REJECT:
            reply = DEGRADED_REPLY
            status = ResultStatus.PARTIAL
        elif verdict.status is VerdictStatus.MASK and verdict.fixed_reply:
            reply = verdict.fixed_reply
            status = ResultStatus.PARTIAL
        elif verdict.status is VerdictStatus.NEEDS_EVIDENCE:
            status = ResultStatus.PARTIAL
        if evidence is not None and (evidence.status is ResultStatus.PARTIAL or evidence.degraded):
            status = ResultStatus.PARTIAL

        return TurnResult(
            reply=reply,
            cites=self._citations(reply, label_to_id),
            verdict=verdict,
            receipts=tuple(receipts),
            degraded=bool(degradation.reasons()) or status is ResultStatus.PARTIAL,
            degraded_ports=degradation.reasons(),
            status=status,
            trace=evidence.trace if evidence else (),
            cost=Cost(
                llm_calls=meter.llm_calls,
                ms=int((time.monotonic() - started) * 1000),
                chars=len(reply),
            ),
            plan=turn_plan,
        )
