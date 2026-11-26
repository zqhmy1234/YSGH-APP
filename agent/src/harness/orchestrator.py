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
    Digest,
    DigestSection,
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
        if self._clock:
            return self._clock.now()
        return datetime.now()  # noqa: DTZ005 —— FakeClock 为 naive，服务内统一 naive UTC

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

        # B5-02 读路径接入（2026-11-24 收口）：
        # 真实请求优先走 backend 的 B5 read_snapshot（/api/v1/memory/agent），
        # 解析成 agent 契约后喂给上下文；backend 不可用/未授权时降级 V3 MemoryStore，
        # 本地无 backend 的测试/角色扮演流程保持可用。
        entries = None
        corrections: tuple[str, ...] = ()
        digest = None
        b5_narrative = ""
        used_b5 = False
        consent_denied = False  # 后端明确拒绝 ⇒ 禁止任何本地降级读取（B6 授权不可绕过）
        if self._backend is not None and getattr(self._backend, "configured", True):
            try:
                from memory.read_model import parse_snapshot

                payload = self._backend.memory(request.person_id)
                # B5 合并：hydrate 复用同一份 payload，避免每轮两次 GET（审判 P1）。
                # 失败不阻断对话（去重窗口拿不到 ≠ 对话失败）。
                hydrate = getattr(self._store, "hydrate", None)
                if callable(hydrate):
                    try:
                        hydrate(request.person_id, payload)
                    except Exception:  # noqa: BLE001
                        degradation.note("memory_hydrate")
                snap = parse_snapshot(payload, expected_user=request.person_id)
                if snap.status == "unavailable":
                    consent_denied = "consent_not_granted" in (snap.note or "")
                    degradation.note("memory_consent" if consent_denied else "memory_backend_unavailable")
                else:
                    used_b5 = True
                    digest = Digest(
                        person_id=request.person_id,
                        sections=tuple(
                            DigestSection(name=str(s.get("name") or ""), text=str(s.get("text") or ""))
                            for s in (snap.digest or {}).get("sections") or ()
                        ),
                        # 状态以 B5 snapshot 为权威；digest 子状态只作参考，避免
                        # snapshot=ok 但 digest 缺失时被误标 unavailable（审判 P1）。
                        state=snap.status,
                    )
                    corrections = tuple(c.value for c in snap.corrections if c.field == "text")
                    if snap.narrative:
                        b5_narrative = "【B5 记忆事实】" + "；".join(e.text for e in snap.narrative[:3]) + "。"
            except Exception:  # noqa: BLE001 —— B5 是可选升级路径，失败必须降级不崩
                degradation.note("b5_memory_fallback")

        if consent_denied:
            # 明确拒绝：记忆内容全部置空，只保留 unavailable 状态；绝不读 V3 本地存储，
            # 否则 backend 的 B6 授权闸被本地未受控副本绕过（审判 P0）。
            entries, corrections, b5_narrative = None, (), ""
            digest = Digest(person_id=request.person_id, state="unavailable")
        elif not used_b5:
            try:
                entries = self._store.entries(request.person_id)
                corrections = tuple(
                    row["value"] for row in self._store.corrections(request.person_id) if row["field"] == "text"
                )
                digest = build_digest(entries, request.person_id)
            except Exception:
                entries, corrections = None, ()
                digest = Digest(person_id=request.person_id, state="unavailable")
                degradation.note("memory")

        # B4 Agent-side wiring: ECHO intent reads read-only candidates from backend.
        # No EchoHistory row / no in-app message is written here — only the user-side
        # /echo/today consumes the daily slot. Backend is optional (local-only runs
        # stay functional); on failure we degrade to no narrative, never invent one.
        echo_narrative = ""
        if turn_plan.intent is Intent.ECHO and self._backend is not None:
            try:
                candidates, suppressed = self._backend.echo(
                    request.person_id,
                    local_date=self._now().date().isoformat(),
                    limit=1,
                )
                if candidates:
                    c = candidates[0]
                    echo_narrative = (
                        "【回响候选·仅供复述，不得新增事实】"
                        f"{c.title}（{c.why_now}）。"
                        "只在这个候选上说话；候选之外的事一律不要提。"
                    )
                elif suppressed:
                    echo_narrative = "【回响候选】今天没有可回响的内容；不要编造回忆，简短回应即可。"
            except Exception:  # noqa: BLE001 — optional backend must never break the turn
                degradation.note("echo")
                echo_narrative = "【回响候选】回响服务暂不可用；不要编造回忆。"

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
            narrative=" ".join(x for x in (b5_narrative, echo_narrative) if x),
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
