"""LiteLLM adapter: one place where provider-specific wire shapes live.

Only this module imports LiteLLM. Tests inject ``FakeEngine`` instead, so the
catalog/router/credential logic stays testable without network or keys.
"""

from __future__ import annotations

import json
from typing import Protocol, runtime_checkable

from .contracts import ModelReply, ModelRequest, ModelToolCall, ProviderId, Usage
from .credentials import Credential
from .errors import ProviderDown, classify


class EngineError(RuntimeError):
    pass


@runtime_checkable
class ModelEngine(Protocol):
    def complete(self, profile, request: ModelRequest, credential: Credential) -> ModelReply: ...


class LiteLLMEngine:
    def __init__(self, *, timeout_s: float = 30.0) -> None:
        self._timeout = timeout_s

    def complete(self, profile, request: ModelRequest, credential: Credential) -> ModelReply:
        try:
            import litellm
        except ImportError as exc:  # pragma: no cover - environment guard
            raise ProviderDown("litellm is not installed") from exc

        model = f"openai/{profile.model}" if credential.gateway else profile.litellm_model
        kwargs: dict = {
            "model": model,
            "messages": [
                {"role": "system", "content": request.system},
                {"role": "user", "content": request.user},
            ],
            "temperature": request.temperature,
            "timeout": self._timeout,
            "api_key": credential.api_key,
            "drop_params": True,
        }
        if credential.base_url:
            kwargs["api_base"] = credential.base_url
        if request.tools:
            kwargs["tools"] = list(request.tools)
            kwargs["tool_choice"] = "auto"
        if profile.provider is ProviderId.KIMI:
            kwargs["max_completion_tokens"] = request.max_tokens
        else:
            kwargs["max_tokens"] = request.max_tokens
        extra = {**profile.extra_body, **request.extra_body}
        if extra:
            kwargs["extra_body"] = extra

        try:
            response = litellm.completion(**kwargs)
        except Exception as exc:  # noqa: BLE001 - provider exceptions are mapped below
            raise classify(exc) from exc

        try:
            choice = response.choices[0]
            message = choice.message
            text = message.content or ""
            raw_calls = getattr(message, "tool_calls", None) or []
            calls = tuple(
                ModelToolCall(
                    name=str(getattr(getattr(call, "function", None), "name", "") or ""),
                    arguments=_json_args(getattr(getattr(call, "function", None), "arguments", "{}")),
                )
                for call in raw_calls
            )
            raw_usage = getattr(response, "usage", None)
            usage = Usage(
                prompt_tokens=int(getattr(raw_usage, "prompt_tokens", 0) or 0),
                completion_tokens=int(getattr(raw_usage, "completion_tokens", 0) or 0),
                total_tokens=int(getattr(raw_usage, "total_tokens", 0) or 0),
            )
            finish = str(getattr(choice, "finish_reason", "stop") or "stop")
            if finish not in {"stop", "tool_calls", "length"}:
                finish = "tool_calls" if calls else "stop"
            return ModelReply(
                text=text,
                tool_calls=calls,
                finish=finish,  # type: ignore[arg-type]
                usage=usage,
                model=str(getattr(response, "model", profile.model) or profile.model),
                profile_id=profile.id,
            )
        except Exception as exc:  # noqa: BLE001 - malformed provider payload is an honest failure
            raise ProviderDown(f"malformed provider response: {type(exc).__name__}") from exc


class FakeEngine:
    """Scripted engine for contract tests; records calls, never touches network."""

    def __init__(self, replies: list[ModelReply] | None = None, *, error: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.error = error
        self.calls: list[tuple[str, ModelRequest, Credential]] = []

    def complete(self, profile, request: ModelRequest, credential: Credential) -> ModelReply:
        self.calls.append((profile.id, request, credential))
        if self.error is not None:
            raise self.error
        if self.replies:
            return self.replies.pop(0)
        return ModelReply(text="我在听。", profile_id=profile.id, model=profile.model)


def _json_args(value: object) -> dict:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str) or not value:
        return {}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


__all__ = ["EngineError", "FakeEngine", "LiteLLMEngine", "ModelEngine"]
