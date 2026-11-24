"""B7-03 EmotionContract: explainable, not falsely precise.

There is deliberately no ``confidence`` field (G9): the model must not present a
made-up probability. ``not_measured`` is a first-class state and must never be
rendered as "平静".
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class EmotionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: Literal["measured", "not_measured"] = "not_measured"
    valence: float | None = Field(None, ge=-1.0, le=1.0, description="效价；未测得为 null")
    arousal: float | None = Field(None, ge=0.0, le=1.0, description="唤醒度；未测得为 null")
    cues: tuple[str, ...] = Field(default_factory=tuple, description="可见线索，必须可溯源")
    basis: str = Field(default="", max_length=200, description="依据；不得写心理诊断")
    display: Literal["off", "gentle", "explicit"] = "gentle"

    def visible(self) -> bool:
        """off 或 not_measured 时客户端不展示情绪卡。"""
        return self.display != "off" and self.state == "measured"


__all__ = ["EmotionContract"]
