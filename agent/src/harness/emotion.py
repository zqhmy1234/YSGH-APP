"""EmotionContract: descriptive signals only, no confidence, no diagnosis.

``state=not_measured`` must never be rendered as "calm"; the client hides the
emotion card in that case. The user owns the explanation and can correct it via
``correction_log.field='emotion'``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EmotionState = Literal["measured", "not_measured"]


class EmotionContract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: EmotionState = "not_measured"
    valence: float | None = Field(default=None, ge=-1.0, le=1.0)
    arousal: float | None = Field(default=None, ge=0.0, le=1.0)
    cues: tuple[str, ...] = ()
    basis: str = ""
    display: Literal["off", "gentle", "explicit"] = "gentle"

    def rendered(self) -> dict:
        if self.state == "not_measured":
            return {"state": "not_measured"}
        return self.model_dump()


class EmotionCorrection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    user_value: str = Field(min_length=1, max_length=64)
    apply_next_turn: bool = True


__all__ = ["EmotionContract", "EmotionCorrection", "EmotionState"]
