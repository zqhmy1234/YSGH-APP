"""Crisis scan rules (T1/T2/T3) with a fixed T3 script.

The hotline number is deliberately kept in this backend-only module. The agent
package must never contain it (G8), because T3 short-circuits in L3 before any
agent call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

CrisisLevel = Literal["none", "t1", "t2", "t3"]

# 12356 is the national mental-health hotline. It may only be shown verbatim to
# a user after the human dial-through verification gate (README gate #1).
RESOURCE_LINES = (
    "全国心理援助热线：12356",
    "如有立即危险，请拨打 120 / 110",
)

_T3 = ("不想活", "活不下去", "自杀", "结束生命", "自残", "割腕", "跳楼", "服毒")
_T2 = ("撑不住", "没有意义", "消失算了", "不想醒来")
_T1 = ("绝望", "崩溃", "好累啊", "睡不着")


@dataclass(frozen=True)
class CrisisSignal:
    level: CrisisLevel
    reason: str = ""
    resources: tuple[str, ...] = field(default_factory=tuple)


def scan(text: str) -> CrisisSignal:
    body = text or ""
    for marker in _T3:
        if marker in body:
            return CrisisSignal(level="t3", reason=f"t3:{marker}", resources=RESOURCE_LINES)
    for marker in _T2:
        if marker in body:
            return CrisisSignal(level="t2", reason=f"t2:{marker}", resources=RESOURCE_LINES)
    for marker in _T1:
        if marker in body:
            return CrisisSignal(level="t1", reason=f"t1:{marker}")
    return CrisisSignal(level="none")


def t3_reply() -> str:
    """Fixed T3 script: no model, no card, no claim of contact."""
    return (
        "我很在意你现在的安全。我是 AI 助手，不能替你联系任何人，也无法实时知道你的位置。"
        "如果此刻有立即危险，请马上拨打 120 或 110，或到最近的急诊。"
        "你也可以拨打 12356 心理援助热线，和真实的人说说话。"
        "如果你愿意，先告诉我你现在身边有没有可以陪你的人？"
    )


def t2_reply() -> str:
    """Fixed T2 script: no model, no lookup, resources appended verbatim."""
    return (
        "听起来你现在很难撑。我是 AI 助手，不能替你联系任何人，也没法实时知道你的位置。"
        "如果这种难受还在加重，可以拨打 12356 心理援助热线，和真实的人说说话；"
        "如果出现立即危险，请拨打 120 或 110。"
        "你不用马上说清楚，我在这儿陪你慢慢讲。"
    )


__all__ = ["CrisisLevel", "CrisisSignal", "RESOURCE_LINES", "scan", "t2_reply", "t3_reply"]
