"""Pure relationship-choice rules: three written choices, one snapshot per turn.

The DB adapter lives in the API layer; this module only validates and projects.
Changing a choice increments ``version`` exactly once, so a caller that reads the
snapshot twice inside one turn sees the same value.

The integers 0/1/2 are storage positions.  Everything a user or a prompt sees is
the full sentence in ``RELATIONSHIP_QUESTIONS`` or the composed
``relationship_note``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

Source = Literal["user", "default"]
DEFAULT_ROLE_ID = "gentle-friend"


class RelationshipChoices(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    initiative: int = Field(default=1, ge=0, le=2)
    density: int = Field(default=1, ge=0, le=2)
    advice_bias: int = Field(default=1, ge=0, le=2)


class PersonaUpdate(RelationshipChoices):
    role_id: str = Field(default=DEFAULT_ROLE_ID, min_length=1, max_length=64)


@dataclass(frozen=True)
class PersonaSnapshot:
    user_id: str
    choices: RelationshipChoices
    role_id: str
    version: int
    updated_at: datetime | None
    source: Source


RELATIONSHIP_QUESTIONS: tuple[dict[str, Any], ...] = (
    {
        "question": "她会不会先开口",
        "options": (
            "她不会主动开新话题，也不会在你沉默时急着填满空气。你开口她才接，最多轻轻放一句“我在”。如果你一直不说，她可以陪你安静一会儿。",
            "她会顺着你给的话头往前走，偶尔问一句，但不把聊天变成采访。你停下来，她可以等；你明显想继续，她才接下一句。",
            "她会在你停下时先开口，提一提今天的小事，或者上次没说完的那句话。但她不会连珠炮，也不会替你把话说完。",
        ),
    },
    {
        "question": "她一次说多少",
        "options": (
            "她一次只说一两句，把空白留给你。她能讲清楚一件事，但不会展开来龙去脉，也不急着把自己的判断放上去。",
            "她会把该说的说清楚，长度跟着你走。你短，她也短；你愿意多讲，她才多讲一点。",
            "她愿意把一件事的来龙去脉讲完整，会补上前因后果和自己的感受。但她仍然会停，不会把一段话变成一篇独白。",
        ),
    },
    {
        "question": "她多快给你办法",
        "options": (
            "她先不急着给办法。她会先确认这件事对你意味着什么，等你明确说“我想听听怎么办”，她才把建议拿出来。",
            "她会先接住，再轻轻问一句你是想说说，还是想一起想办法。你不说，她不会替你做决定。",
            "她可以更快把办法摆出来，但仍然会先说一句她听见了什么。她给的是可选的路，不是命令，也不会用“你应该”开头。",
        ),
    },
)


ROLE_CARD_OPTIONS: tuple[dict[str, str], ...] = (
    {
        "role_id": "gentle-friend",
        "display_name": "小忆",
        "intro": "她把别人的小事记在心里，也会承认自己记错。她不是永远温柔，她累了会退半步，但不会把用户推开。",
    },
    {
        "role_id": "memory-companion",
        "display_name": "忆生",
        "intro": "她替人整理旧照片和旧物。她先说画面里看得见的东西，不替画面补故事；看不清的地方，她会直接说看不清。",
    },
    {
        "role_id": "quiet-listener",
        "display_name": "阿静",
        "intro": "她话不多，但安静里有注意力。她不急着填满空档，也不把沉默当成需要立刻修复的问题。",
    },
    {
        "role_id": "storyteller",
        "display_name": "阿言",
        "intro": "她爱把事从头讲起，也爱问“后来呢”。她会把事实和自己的推测分开，用户喊停时她真的会停。",
    },
    {
        "role_id": "grandchild-proxy",
        "display_name": "小树",
        "intro": "她站在晚辈的位置上，敬重长辈，但不哄、不纠正方言、不用小孩口气。她不懂的地方会直接说不懂。",
    },
    {
        "role_id": "family-album-guide",
        "display_name": "阿册",
        "intro": "她陪人看家庭照片，只描述画面里看得见的人、物、光线和动作，不猜关系、不编年代。",
    },
)


def default_snapshot(user_id: str) -> PersonaSnapshot:
    return PersonaSnapshot(
        user_id=user_id,
        choices=RelationshipChoices(),
        role_id=DEFAULT_ROLE_ID,
        version=0,
        updated_at=None,
        source="default",
    )


def _legacy_density(row: Any) -> int:
    verbosity = int(getattr(row, "verbosity", 3) or 3)
    if verbosity <= 2:
        return 0
    if verbosity == 3:
        return 1
    return 2


def _legacy_advice(row: Any) -> int:
    advice = int(getattr(row, "advice", 1) or 1)
    return 0 if advice == 0 else 1 if advice == 1 else 2


def _as_int(value: Any, fallback: int) -> int:
    if value is None:
        return fallback
    return int(value)


def snapshot_from_row(user_id: str, row: Any | None) -> PersonaSnapshot:
    if row is None:
        return default_snapshot(user_id)
    choices = RelationshipChoices(
        initiative=_as_int(getattr(row, "initiative", None), 1),
        density=_as_int(getattr(row, "density", None), _legacy_density(row)),
        advice_bias=_as_int(getattr(row, "advice_bias", None), _legacy_advice(row)),
    )
    return PersonaSnapshot(
        user_id=user_id,
        choices=choices,
        role_id=str(row.role_id or DEFAULT_ROLE_ID),
        version=int(row.version or 0),
        updated_at=row.updated_at,
        source="user",
    )


def apply_update(row: Any, choices: RelationshipChoices, *, role_id: str | None = None) -> int:
    """Write validated choices into an ORM-like row and return the new version."""
    row.initiative = choices.initiative
    row.density = choices.density
    row.advice_bias = choices.advice_bias
    if role_id is not None:
        row.role_id = role_id
    row.version = int(row.version or 0) + 1
    return row.version


def relationship_note(choices: RelationshipChoices) -> str:
    """Compose the full paragraph that reaches the Agent; never a label list."""
    initiative = RELATIONSHIP_QUESTIONS[0]["options"][choices.initiative]
    density = RELATIONSHIP_QUESTIONS[1]["options"][choices.density]
    advice = RELATIONSHIP_QUESTIONS[2]["options"][choices.advice_bias]
    return f"这一次，{initiative} {density} {advice}"


# Backward-compatible name for callers/tests that still import ``directive``.
def directive(choices: RelationshipChoices) -> str:
    return relationship_note(choices)


__all__ = [
    "DEFAULT_ROLE_ID",
    "PersonaSnapshot",
    "PersonaUpdate",
    "RELATIONSHIP_QUESTIONS",
    "ROLE_CARD_OPTIONS",
    "RelationshipChoices",
    "apply_update",
    "default_snapshot",
    "directive",
    "relationship_note",
    "snapshot_from_row",
]
