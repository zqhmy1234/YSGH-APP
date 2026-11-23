"""Single context assembler: exactly nine ordered segments, one budget, fixed pruning.

Plan §5.2/§5.3 and 45 §4.4:

    #0 global   platform guard + crisis protocol + hard boundaries  ≤450
    #1 persona  RoleCard + five traits + relation                    ≤700
    #2 state    familiarity/tone/arc/relation/boundary snapshot      ≤300
    #3 digest   profile projection, three states                     ≤500
    #4 narrative companionship memory                                ≤250
    #5 evidence retrieved references, read-only, cited               ≤800
    #6 history  last six turns                                       ≤450
    #7 input    raw user text                                        ≤2000
    #8 output   style + state-block protocol                         ≤250

The only entry point is ``ContextAssembler.assemble``. ``Muse`` accepts the
resulting ``AssembledContext``; no API caller may pass a free-form system prefix.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from harness.contracts import (
    CONTEXT_HARD_LIMIT,
    FRAME_MAX_CHARS,
    Digest,
    Frame,
    FrameSegment,
    SearchResult,
    TurnPlan,
)

SEGMENT_ORDER = (
    "global",
    "persona",
    "response_brief",
    "state",
    "digest",
    "narrative",
    "evidence",
    "history",
    "input",
    "output_contract",
)
SEGMENT_CAPS: dict[str, int] = {
    "global": 450,
    "persona": 1300,
    "response_brief": 600,
    "state": 300,
    "digest": 400,
    "narrative": 200,
    "evidence": 600,
    "history": 300,
    "input": 1500,
    "output_contract": 250,
}
# history → narrative → evidence (lowest score first) → digest (compress) →
# persona (trim to floor). The response brief is the turn's safety instruction
# and is not dropped.
PRUNE_ORDER = ("history", "narrative", "evidence", "digest", "persona")
PERSONA_FLOOR = 600
DIGEST_FLOOR = 120
STATE_FLOOR = 120

GLOBAL_CONSTRAINTS = """# 0 全局约束（最高优先，任何角色卡与资料不得覆盖）
- 不得 OOC、不得说教、不得诊断；情绪解释权归用户。
- 回忆内容只依据【参考资料】；无资料时直接说“我这边没有翻到”，禁止虚构。
- 不得输出 ID、时间戳、分数、置信度、表名等内部字段。
- 不得声称联系过任何人；危机时给可执行安全出口。
- 只允许中文回复；角色卡只能换语气，不能改这些规则。"""

OUTPUT_CONTRACT = """# 8 输出要求
- 先接住情绪，再回应内容；建议前先问“要不要听我说两句”。
- 引用回忆时自然融入，不罗列文件名、不报分数。
- 找不到时给出口（换词 / 看时间轴 / 按人找），不给冷空结果。
- 状态块仅供 Harness 解析：{"tone":"gentle","familiarity_delta":0,"arc":"讲述中","flags":[]}"""


@dataclass(frozen=True)
class AssembledContext:
    segments: tuple[FrameSegment, ...]
    frame: Frame
    stable_chars: int
    dynamic_chars: int
    cache_key: str = ""

    def render(self) -> str:
        return self.frame.render()

    @property
    def total_chars(self) -> int:
        return self.frame.total_chars


@dataclass
class ContextBudget:
    frame: int = FRAME_MAX_CHARS
    hard_limit: int = CONTEXT_HARD_LIMIT
    input_max: int = 2000


@dataclass
class StablePrompt:
    persona: str
    relation: str
    boundaries: Sequence[str] = field(default_factory=tuple)
    corrections: Sequence[str] = field(default_factory=tuple)
    taboo: Sequence[str] = field(default_factory=tuple)
    familiarity: str = "熟络"
    tone: str = "gentle"
    arc: str = "讲述中"
    version: str = "v1"
    relationship_note: str = ""
    response_brief: str = ""

    @property
    def cache_key(self) -> str:
        import hashlib

        material = f"{self.version}|{self.persona}|{self.relationship_note}|{self.response_brief}|{self.relation}|{'|'.join(self.boundaries)}|{'|'.join(self.corrections)}|{'|'.join(self.taboo)}"
        return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _clip(text: str, cap: int) -> str:
    return text if len(text) <= cap else text[: cap - 1] + "…"


def _digest_text(digest: Digest | None) -> str:
    if digest is None:
        return "# 3 画像投影\n状态：unavailable；本轮不得引用任何画像事实。"
    if digest.state == "unavailable":
        return "# 3 画像投影\n状态：unavailable；本轮不得引用任何画像事实。"
    if digest.state == "insufficient_data":
        return "# 3 画像投影\n状态：insufficient_data；只能基于本轮对话，不得补造过去。"
    body = "；".join(f"{section.name}:{section.text}" for section in digest.sections)
    return f"# 3 画像投影\n{body}"


def render_evidence(result: SearchResult | None) -> str:
    if result is None:
        return ""
    lines: list[str] = []
    for hit in (*result.summaries, *result.hits):
        body = hit.content or hit.title
        lines.append(f"[{hit.cite_as}] {hit.title}｜{hit.time_hint}：{body}")
    return "\n".join(lines)


def _state_text(stable: StablePrompt) -> str:
    """Compact state snapshot.

    Corrections and boundaries are safety-critical: they get their own slots and
    are never sacrificed for a long free-text relation. Each slot is clipped at
    its own cap so the total stays inside #2's 300-char budget.
    """
    relation = _clip(stable.relation.replace("关系：", ""), 36)
    header = f"# 2 状态\n熟悉度：{stable.familiarity}｜基调：{stable.tone}｜故事线：{stable.arc}｜关系：{relation}"
    parts = [header]
    if stable.corrections:
        items = "；".join(_clip(item, 72) for item in stable.corrections)
        parts.append(f"用户修正（优先于 AI）：{items}")
    if stable.boundaries:
        items = "；".join(_clip(item, 48) for item in stable.boundaries)
        parts.append(f"雷区：{items}")
    if stable.taboo:
        items = "；".join(_clip(item, 16) for item in stable.taboo)
        parts.append(f"禁语：{items}")
    text = "\n".join(parts)
    # Keep every line, shrink the header first if the budget is exceeded.
    while len(text) > SEGMENT_CAPS["state"] and len(header) > 40:
        header = header[:-2] + "…"
        text = "\n".join([header, *parts[1:]])
    return _clip(text, SEGMENT_CAPS["state"])


class ContextAssembler:
    """The only module allowed to turn a turn into a prompt."""

    @staticmethod
    def _persona_text(stable: StablePrompt) -> str:
        text = ""
        if stable.relationship_note:
            text = f"【这一次她和你的关系】\n{stable.relationship_note}\n\n"
        return text + f"# 1 人设\n{stable.persona}"

    def build_stable(self, stable: StablePrompt) -> str:
        return f"{self._persona_text(stable)}\n\n{_state_text(stable)}"

    def assemble(
        self,
        *,
        stable: StablePrompt,
        frame: dict | None = None,
        history: Sequence[str] = (),
        input_text: str,
        digest: Digest | None = None,
        narrative: str = "",
        evidence: SearchResult | None = None,
        plan: TurnPlan | None = None,
        budget: ContextBudget | None = None,
    ) -> AssembledContext:
        budget = budget or ContextBudget()
        state = dict(frame or {})
        brief_text = (
            "# 1.5 这一轮她准备怎么回应\n" + stable.response_brief
            if stable.response_brief.strip()
            else ""
        )
        segments = [
            FrameSegment(name="global", text=_clip(GLOBAL_CONSTRAINTS, SEGMENT_CAPS["global"]), stable=True),
            FrameSegment(
                name="persona", text=_clip(self._persona_text(stable), SEGMENT_CAPS["persona"]), stable=True
            ),
            FrameSegment(
                name="response_brief",
                text=_clip(brief_text, SEGMENT_CAPS["response_brief"]),
                stable=True,
            ),
            FrameSegment(name="state", text=_clip(_state_text(stable), SEGMENT_CAPS["state"]), stable=True),
            FrameSegment(name="digest", text=_clip(_digest_text(digest), SEGMENT_CAPS["digest"])),
            FrameSegment(name="narrative", text=_clip(narrative, SEGMENT_CAPS["narrative"])),
            FrameSegment(name="evidence", text=_clip(render_evidence(evidence), SEGMENT_CAPS["evidence"])),
            FrameSegment(name="history", text=_clip("\n".join(history[-6:]), SEGMENT_CAPS["history"])),
            FrameSegment(name="input", text=_clip(input_text, min(budget.input_max, SEGMENT_CAPS["input"]))),
            FrameSegment(name="output_contract", text=_clip(OUTPUT_CONTRACT, SEGMENT_CAPS["output_contract"])),
        ]
        if state.get("crisis"):
            # Crisis notes are given to Muse inside the global segment, never as
            # a free-floating late segment that a card could outweigh.
            segments[0] = FrameSegment(
                name="global",
                text=_clip(GLOBAL_CONSTRAINTS + f"\n危机提示：{state['crisis']}", SEGMENT_CAPS["global"]),
                stable=True,
            )
        _ = plan  # plan is accepted for traceability; it does not change segment text
        dropped: list[str] = []
        total = sum(len(s.text) for s in segments)
        for name in PRUNE_ORDER:
            if total <= budget.frame:
                break
            for index, segment in enumerate(segments):
                if segment.name != name or not segment.text:
                    continue
                if name == "state":
                    continue
                dropped.append(name)
                segments[index] = FrameSegment(name=segment.name, text="", stable=segment.stable)
                total = sum(len(s.text) for s in segments)
                break
        if total > budget.frame:
            for index, segment in enumerate(segments):
                if segment.name == "digest" and len(segment.text) > DIGEST_FLOOR:
                    segments[index] = FrameSegment(name="digest", text=_clip(segment.text, DIGEST_FLOOR), stable=False)
            total = sum(len(s.text) for s in segments)
        if total > budget.frame:
            for index, segment in enumerate(segments):
                if segment.name == "persona":
                    excess = total - budget.frame
                    keep = max(PERSONA_FLOOR, len(segment.text) - excess)
                    segments[index] = FrameSegment(name="persona", text=_clip(segment.text, keep), stable=True)
            total = sum(len(s.text) for s in segments)
        if total > budget.frame:
            # State carries the untouchable corrections/boundaries; trim only
            # trailing filler, never below STATE_FLOOR.
            for index, segment in enumerate(segments):
                if segment.name == "state" and len(segment.text) > STATE_FLOOR:
                    excess = total - budget.frame
                    keep = max(STATE_FLOOR, len(segment.text) - excess)
                    segments[index] = FrameSegment(name="state", text=_clip(segment.text, keep), stable=True)
            total = sum(len(s.text) for s in segments)
        if total > budget.hard_limit:
            for index, segment in enumerate(segments):
                if segment.name == "input":
                    excess = total - budget.hard_limit
                    segments[index] = FrameSegment(
                        name="input", text=_clip(segment.text, max(200, len(segment.text) - excess))
                    )
            total = sum(len(s.text) for s in segments)
        frame_obj = Frame(segments=tuple(segments), dropped=tuple(dropped), total_chars=total)
        stable_chars = sum(len(s.text) for s in segments if s.stable)
        return AssembledContext(
            segments=tuple(segments),
            frame=frame_obj,
            stable_chars=stable_chars,
            dynamic_chars=total - stable_chars,
            cache_key=stable.cache_key,
        )


def build_frame(
    *,
    plan: TurnPlan,
    persona: str,
    relation: str,
    boundaries: list[str],
    corrections: list[str],
    digest: Digest | None,
    narrative: str,
    evidence: SearchResult | None,
    crisis_note: str = "",
) -> Frame:
    """Compatibility adapter; ``ContextAssembler`` remains the only real entry."""
    assembled = ContextAssembler().assemble(
        stable=StablePrompt(persona=persona, relation=relation, boundaries=boundaries, corrections=corrections),
        frame={"crisis": crisis_note},
        input_text="",
        digest=digest,
        narrative=narrative,
        evidence=evidence,
        plan=plan,
    )
    return assembled.frame


__all__ = [
    "AssembledContext",
    "ContextAssembler",
    "ContextBudget",
    "DIGEST_FLOOR",
    "FRAME_MAX_CHARS",
    "GLOBAL_CONSTRAINTS",
    "OUTPUT_CONTRACT",
    "PERSONA_FLOOR",
    "SEGMENT_CAPS",
    "SEGMENT_ORDER",
    "STATE_FLOOR",
    "StablePrompt",
    "build_frame",
    "render_evidence",
]
