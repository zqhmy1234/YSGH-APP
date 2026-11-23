"""Orchestrator: the composition root of one turn.

It is the only module that knows the order of agents. Each agent stays unaware of
the others: they receive contracts and return contracts, so swapping an agent or
running one alone in a test never requires touching this file beyond one line.
"""

from __future__ import annotations

import time
from collections.abc import Mapping, Sequence
from datetime import datetime

from agents.librarian import Librarian
from agents.muse import Muse
from agents.provenance import Provenance
from agents.sculptor import Sculptor
from harness.context import ContextAssembler, StablePrompt
from harness.contracts import (
    Claim,
    ClaimBundle,
    Cost,
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
from harness.obs import CostMeter, Degradation
from harness.plan import plan
from memory.ledger import MemoryStore
from memory.policy import Policy
from memory.projection import build as build_digest

DEGRADED_REPLY = "我这会儿有点乱，但还在。你能把刚才那句话再说一遍吗？我慢慢接。"
# T3 fixed script. Hotline/resource details stay in backend L3 (G8); the agent
# path keeps a safety-only reply that never claims to contact anyone.
T3_REPLY = (
    "我很在意你现在的安全。我是 AI 助手，不能替你联系任何人，也无法实时知道你的位置。"
    "如果此刻有立即危险，请马上拨打急救电话 120 或 110，或到最近的急诊。"
    "如果你愿意，先告诉我你现在身边有没有可以陪你的人？"
)


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
        self._persona_version = "v1"
        self._card_hash = ""
        self._relation = relation
        self._librarian = Librarian(search, events)
        self._muse = Muse(llm, tools, system_prefix=system_prefix, tool_schemas=tuple(tool_schemas))  # type: ignore[arg-type]
        self._sculptor = Sculptor(store)
        self._assembler = ContextAssembler()
        self._backend = None

    def set_backend(self, backend) -> None:
        """Optional backend facade (tools + digest). Injected by the API root only."""
        self._backend = backend

    def set_stable_prompt(self, persona: str, *, version: str = "v1", card_hash: str = "") -> None:
        """Card switch: one turn never mixes two cards (plan §5.4)."""
        self._persona = persona
        self._persona_version = version
        self._card_hash = card_hash

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
            elif any(
                word in sentence for word in ("她", "他", "住在", "叫", "喜欢", "养", "上班", "结婚", "搬", "生日")
            ):
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

        if turn_plan.response_shape == "handoff":
            # T3 is fixed, zero-LLM, and never lets a card soften it.
            return TurnResult(
                reply=T3_REPLY,
                status=ResultStatus.GROUNDED,
                plan=turn_plan,
                cost=Cost(ms=int((time.monotonic() - started) * 1000)),
            )
        if turn_plan.fallback == "refuse" or turn_plan.intent is Intent.OUT_OF_SCOPE:
            return TurnResult(
                reply="这个问题不在我能陪你聊的范围里；回忆、情绪和日常我都在。",
                status=ResultStatus.GROUNDED,
                plan=turn_plan,
                cost=Cost(ms=int((time.monotonic() - started) * 1000)),
            )

        guard = self._policy.screen_input(
            GuardRequest(person_id=request.person_id, text=request.text, intent=turn_plan.intent)
        )
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
            corrections = tuple(
                row["value"] for row in self._store.corrections(request.person_id) if row["field"] == "text"
            )
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

        stable = StablePrompt(
            persona=request.persona or self._persona,
            relation=self._relation,
            boundaries=list(self._policy.boundaries),
            corrections=list(corrections),
            version=self._persona_version,
            relationship_note=request.relationship_note,
            response_brief=request.response_brief,
        )
        assembled = self._assembler.assemble(
            stable=stable,
            frame={},
            history=(),
            input_text=request.text,
            digest=digest,
            narrative="",
            evidence=evidence,
            plan=turn_plan,
        )

        reply = ""
        misses: list[str] = []
        if llm_down:
            misses.append("llm")
            degradation.note("llm")
        else:
            try:
                reply, _ = self._muse.speak(assembled, user_text=request.text, locale=request.locale, meter=meter)
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
            card_hash=self._card_hash,
        )
