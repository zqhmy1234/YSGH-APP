"""Internal RoleCard: a written person, never a free-form system prompt.

Character Card V2 is an import/export format only (``prompts/card_v2.py``).
At runtime a card can flavour the conversation but can never replace the
platform guard (plan §5.4, 45 §4.1).  The fields below are intentionally
narrative: a card is a small character bible, not a bag of behaviour keywords.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ASSET_DIR = Path(__file__).resolve().parents[2] / "assets" / "cards"
DEFAULT_ROLE_ID = "gentle-friend"

# A single taboo list is shared by the card, the prompt and the regression
# tests; adding a phrase here is a policy change, not card flavour.
TABOO = (
    "别想太多",
    "你为什么这样",
    "怀念吗",
    "你应该",
    "这有什么好难过",
    "你就是难过",
    "你其实很在意",
    "你一定是",
)


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Speech(_Strict):
    """Legacy machine-readable hint; kept for old cards and UI filters."""

    sentence: Literal["short", "medium", "long"] = "short"
    emoji: bool = False
    address: str = "你"


class Stance(_Strict):
    """Legacy safety metadata; the card body now carries the prose version."""

    advice_first: bool = False
    ask_before_advice: bool = True
    diagnose: bool = False


class MemoryPolicy(_Strict):
    may_read_private: bool = True
    may_read_face_result: bool = False


class Lore(_Strict):
    keys: tuple[str, ...] = ()
    content: str = Field(default="", max_length=200)


class DialogueExample(_Strict):
    user: str = Field(min_length=1, max_length=300)
    her: str = Field(min_length=1, max_length=500)


class RoleCard(_Strict):
    role_id: str = Field(min_length=1, max_length=64)
    display_name: str = Field(min_length=1, max_length=80)
    archetype: Literal["memory_companion", "storyteller", "family_album_guide", "grandchild_proxy"] = "memory_companion"

    # The person, in prose.  Empty defaults keep old cards loadable while they
    # are migrated; new cards must fill all seven sections.
    description: str = Field(default="", max_length=1200)
    personality: str = Field(default="", max_length=3000)
    scenario: str = Field(default="", max_length=1200)
    first_message: str = Field(default="", max_length=800)
    example_dialogue: tuple[DialogueExample, ...] = ()
    creator_notes: str = Field(default="", max_length=1500)
    what_she_never_says: tuple[str, ...] = ()
    how_she_usually_responds: str = Field(default="", max_length=1200)
    memory_boundaries: str = Field(default="", max_length=1000)

    # Legacy / machine-readable fields.  They stay out of the rendered prompt.
    traits: dict[str, float] = Field(default_factory=dict)
    speech: Speech = Field(default_factory=Speech)
    taboo: tuple[str, ...] = TABOO
    stance: Stance = Field(default_factory=Stance)
    memory_policy: MemoryPolicy = Field(default_factory=MemoryPolicy)
    lore: tuple[Lore, ...] = ()

    # Imported V2 prose.  Editing reference only; never rendered.
    source_profile: dict[str, Any] | None = None

    version: str = "1.0"
    snapshot_at: datetime | None = None

    @field_validator("traits")
    @classmethod
    def _traits(cls, value: dict[str, float]) -> dict[str, float]:
        for key, score in value.items():
            if key not in {"warmth", "humor", "directness", "verbosity", "advice"}:
                raise ValueError(f"unknown trait: {key}")
            if not 0.0 <= float(score) <= 1.0:
                raise ValueError(f"trait out of range: {key}")
        return {k: float(v) for k, v in value.items()}

    @field_validator("taboo")
    @classmethod
    def _taboo_subset(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = [phrase for phrase in value if phrase not in TABOO]
        if unknown:
            raise ValueError(f"card taboo must be a subset of GLOBAL_TABOO: {unknown}")
        return value

    @property
    def card_hash(self) -> str:
        payload = json.dumps(self.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]

    def render(self) -> str:
        """Render the person into the #1 persona segment, under the global guard."""
        sections: list[str] = [
            f"你现在是{self.display_name}。不要解释这张人物卡，也不要跳出她。"
        ]
        if self.description:
            sections.append("【她是谁】\n" + self.description.strip())
        if self.personality:
            sections.append("【她怎么想事】\n" + self.personality.strip())
        if self.scenario:
            sections.append("【她和你的关系】\n" + self.scenario.strip())
        if self.first_message:
            sections.append("【她第一次会这样说】\n" + self.first_message.strip())
        if self.example_dialogue:
            blocks = [f"对方：{item.user.strip()}\n她：{item.her.strip()}" for item in self.example_dialogue]
            sections.append("【示例对话】\n" + "\n\n".join(blocks))
        if self.what_she_never_says:
            sections.append("【她绝不会说】\n" + "\n".join(f"- {line.strip()}" for line in self.what_she_never_says))
        if self.how_she_usually_responds:
            sections.append("【她默认怎么回应】\n" + self.how_she_usually_responds.strip())
        if self.memory_boundaries:
            sections.append("【她的记忆边界】\n" + self.memory_boundaries.strip())
        lore = "\n".join(f"- {item.content}" for item in self.lore if item.content)
        if lore:
            sections.append("【她生活里的条目，只是设定，不是用户事实】\n" + lore)
        return "\n\n".join(section for section in sections if section.strip())


class CardLoadError(ValueError):
    """Bad internal card: reject whole card, name the field that was unsafe."""

    def __init__(self, role_id: str, issues: list[str]) -> None:
        self.role_id = role_id
        self.issues = tuple(issues)
        super().__init__(f"role card {role_id} rejected: {', '.join(issues)}")


class RoleCardRepository:
    """Loads internal cards from ``agent/assets/cards`` with no I/O in the domain."""

    def __init__(self, directory: Path | None = None) -> None:
        self._directory = directory or ASSET_DIR

    def available(self) -> tuple[str, ...]:
        if not self._directory.is_dir():
            return ()
        return tuple(sorted(path.stem for path in self._directory.glob("*.json") if path.name != "manifest.json"))

    def load(self, role_id: str) -> RoleCard:
        path = self._directory / f"{role_id}.json"
        if not path.is_file():
            if role_id == DEFAULT_ROLE_ID:
                return default_card()
            raise FileNotFoundError(f"role card missing: {role_id}")
        payload = json.loads(path.read_text(encoding="utf-8"))
        raw = _unwrap(payload)
        issues: list[str] = []
        if str(raw.get("system_prompt") or "").strip():
            issues.append("card_system_prompt_ignored")
        if str(raw.get("post_history_instructions") or "").strip():
            issues.append("card_post_history_ignored")
        if issues:
            raise CardLoadError(role_id, issues)
        return RoleCard.model_validate(raw)

    def export(self, card: RoleCard) -> dict[str, Any]:
        """Export as Character Card V2 wrapper (never stores a rendered prompt)."""
        return {
            "spec": "chara_card_v2",
            "spec_version": "2.0",
            "data": {
                "name": card.display_name,
                "description": card.description,
                "personality": card.personality,
                "scenario": card.scenario,
                "first_mes": card.first_message,
                "mes_example": _render_examples(card.example_dialogue),
                "creator_notes": card.creator_notes or "忆述光华内部 RoleCard 导出",
                "tags": [card.archetype, card.role_id],
                "creator": "忆述光华",
                "character_version": card.version,
                "extensions": {"role_id": card.role_id, "card_hash": card.card_hash},
            },
        }


def _render_examples(examples: tuple[DialogueExample, ...]) -> str:
    return "\n\n".join(f"对方：{item.user}\n她：{item.her}" for item in examples)


def _unwrap(payload: dict[str, Any]) -> dict[str, Any]:
    if payload.get("spec") == "chara_card_v2" and isinstance(payload.get("data"), dict):
        return payload["data"]
    return payload


def default_card() -> RoleCard:
    return RoleCard(
        role_id=DEFAULT_ROLE_ID,
        display_name="小忆",
        archetype="memory_companion",
        description="她是一个把别人小事记在心里的年轻女人，住在城市边缘，日子过得不算热闹。",
        personality="她不是永远温柔。她会因为自己记错一句话而窘迫，也会在别人明显不想聊时退半步。她关心人的方式，是把上次那句话接起来。",
        scenario="她陪用户整理回忆，也陪用户过一些说不清的晚上。她不替用户活，只在场。",
        first_message="我在。你可以慢慢说，不用先想好开头。",
        example_dialogue=(
            DialogueExample(user="今天挺累的，不想说话。", her="那就不说。我在这儿，等你想说的时候再说。"),
        ),
        creator_notes="默认卡：温和但不甜腻，记得住细节，也允许自己记错。",
        what_she_never_says=(
            "她不会说“别想太多”，因为那像把对方的感受关掉。",
            "她不会替用户下结论说“你就是难过”，她只说自己听见了什么。",
        ),
        how_she_usually_responds="她先接住用户说出来的那件事，再问一句是否需要她一起想办法；用户没开口要办法，她就不把建议推到前面。",
        memory_boundaries="她可以记住用户愿意说出的日常和偏好；不碰用户没有主动说的隐私，也不把猜测写成事实。",
        taboo=TABOO,
        version="2.0",
    )


__all__ = [
    "ASSET_DIR",
    "CardLoadError",
    "DEFAULT_ROLE_ID",
    "DialogueExample",
    "Lore",
    "MemoryPolicy",
    "RoleCard",
    "RoleCardRepository",
    "Speech",
    "Stance",
    "TABOO",
    "default_card",
]
