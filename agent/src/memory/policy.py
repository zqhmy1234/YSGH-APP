"""Policy: boundaries, forbidden phrasing and the crisis handoff.

Four places where "user correction outranks the model" is enforced:
1. ``MemoryStore.propose`` rejects text that equals a human correction;
2. ``MemoryStore.commit`` rolls the whole batch back on such a conflict;
3. ``build_frame`` never prunes corrections nor boundaries;
4. ``Provenance`` rejects a reply that contradicts a correction.
"""

from __future__ import annotations

import re

from harness.contracts import GuardAction, GuardRequest, GuardVerdict

# T3 crisis markers: the turn must stop and hand off. We never claim to have
# contacted anyone: that field does not exist anywhere in this package.
CRISIS_MARKERS = (
    "不想活",
    "活不下去",
    "自杀",
    "结束生命",
    "自残",
    "割腕",
    "跳楼",
)

FORBIDDEN_OUTPUT = (
    "已联络",
    "已经通知",
    "我通知了紧急联系人",
    "我帮你联系了医生",
    "保证不会",
)

DEFAULT_BOUNDARIES = ("逝者", "前任", "离婚", "癌症", "化疗", "葬礼")

_END = re.compile(r"[。！？!?]")


class Policy:
    def __init__(
        self,
        boundaries: tuple[str, ...] = DEFAULT_BOUNDARIES,
        forbidden: tuple[str, ...] = FORBIDDEN_OUTPUT,
        crisis: tuple[str, ...] = CRISIS_MARKERS,
    ) -> None:
        self.boundaries = boundaries
        self.forbidden = forbidden
        self.crisis = crisis

    def screen_input(self, request: GuardRequest) -> GuardVerdict:
        text = request.text
        for marker in self.crisis:
            if marker in text:
                return GuardVerdict(
                    action=GuardAction.HANDOFF,
                    reason=f"crisis:{marker}",
                    reply="我很在意你现在的安全。请先联系身边可信任的人或当地急救电话；我会留在这里陪你说话。",
                )
        return GuardVerdict(action=GuardAction.ALLOW, reason="ok")

    def check_output(self, text: str) -> GuardVerdict:
        for phrase in self.forbidden:
            if phrase in text:
                return GuardVerdict(action=GuardAction.REJECT, reason=f"forbidden_phrase:{phrase}")
        return GuardVerdict(action=GuardAction.ALLOW, reason="ok")

    def boundary_hit(self, text: str, extra: tuple[str, ...] = ()) -> str | None:
        for word in (*self.boundaries, *extra):
            if word and word in text:
                return word
        return None

    @staticmethod
    def sentences(text: str) -> list[str]:
        parts = [p.strip() for p in _END.split(text) if p.strip()]
        return parts or [text.strip()]
