"""Lingqu contracts: the only data shapes the model gateway exchanges.

No provider SDK types leak past this module. ``ModelProfile`` is a catalog row;
``ModelRequest`` is one normalized call; ``ModelReply`` is what the rest of
agentv3 sees after LiteLLM has been adapted back to our own vocabulary.
"""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class LingquModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ProviderId(str, Enum):
    DEEPSEEK = "deepseek"
    QWEN = "qwen"
    KIMI = "kimi"
    MINIMAX = "minimax"
    GLM = "glm"


class ApiMode(str, Enum):
    OPENAI_CHAT = "openai_chat"
    ANTHROPIC = "anthropic"


class ModelProfile(LingquModel):
    id: str = Field(min_length=3, max_length=100)
    provider: ProviderId
    model: str = Field(min_length=1, max_length=100)
    litellm_model: str = Field(min_length=3, max_length=120)
    base_url: str = ""
    base_url_fallback: str = ""
    api_mode: ApiMode = ApiMode.OPENAI_CHAT
    context_window: int = Field(ge=1024, le=2_000_000)
    max_output: int = Field(ge=256, le=1_000_000)
    capabilities: tuple[str, ...] = ("chat",)
    params: dict[str, Any] = Field(default_factory=dict)
    extra_body: dict[str, Any] = Field(default_factory=dict)
    credential: str = "company_llm"
    source: str = ""
    verified_at: str = ""

    @field_validator("capabilities")
    @classmethod
    def _caps(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        known = {"chat", "tools", "json", "reasoning", "search", "vision"}
        unknown = set(value) - known
        if unknown:
            raise ValueError(f"unknown capabilities: {sorted(unknown)}")
        return value


class ModelToolCall(LingquModel):
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, Any] = Field(default_factory=dict)


class Usage(LingquModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0


class ModelRequest(LingquModel):
    purpose: Literal["muse", "librarian_rewrite", "provenance", "sculptor", "digest"] = "muse"
    system: str = Field(min_length=1, max_length=8000)
    user: str = Field(min_length=1, max_length=8000)
    tools: tuple[dict[str, Any], ...] = ()
    max_tokens: int = Field(ge=50, le=8192, default=1200)
    temperature: float = Field(ge=0.0, le=2.0, default=0.3)
    extra_body: dict[str, Any] = Field(default_factory=dict)


class ModelReply(LingquModel):
    text: str
    tool_calls: tuple[ModelToolCall, ...] = ()
    finish: Literal["stop", "tool_calls", "length"] = "stop"
    usage: Usage = Field(default_factory=Usage)
    model: str = ""
    profile_id: str = ""


class HealthState(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"


class Health(LingquModel):
    profile_id: str
    state: HealthState = HealthState.OK
    detail: str = ""
    latency_ms: int | None = None
