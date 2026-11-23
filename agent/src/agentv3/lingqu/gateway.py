"""LingquGateway: the only entry point business code should use.

It implements ``LLMPort`` so ``Muse`` stays unaware of providers. One call may
try more than one profile, but only for provider failures; auth/parameter errors
are surfaced as-is. Credentials are resolved per candidate, so a per-provider
key can rescue a company-key profile.
"""

from __future__ import annotations

import os
import time
from typing import Mapping

from ..contracts import LLMRequest, LLMResponse, ToolCall
from ..ports import PortError, PortTimeout, PortUnavailable
from .catalog import Catalog, CatalogError, load_catalog
from .contracts import Health, HealthState, ModelReply, ModelRequest, ModelProfile
from .credentials import MissingCredential, SecretResolver
from .engine import LiteLLMEngine, ModelEngine
from .errors import LingquError, ProviderTimeout, should_fallback
from .router import Route, RoutingPolicy
from .telemetry import Telemetry


class LingquGateway:
    def __init__(self, *, catalog: Catalog | None = None, resolver: SecretResolver | None = None, engine: ModelEngine | None = None, policy: RoutingPolicy | None = None, telemetry: Telemetry | None = None, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(os.environ if env is None else env)
        self._catalog = catalog or load_catalog(self._env.get("LINGQU_CATALOG_PATH"))
        self._resolver = resolver or SecretResolver(self._env)
        self._engine = engine or LiteLLMEngine(timeout_s=float(self._env.get("LINGQU_TIMEOUT_S", "30") or 30))
        self._policy = policy or RoutingPolicy(self._catalog, self._env)
        self.telemetry = telemetry or Telemetry()
        self._active = self._env.get("LINGQU_DEFAULT_PROFILE") or self._catalog.default_profile
        if not self._catalog.has(self._active):
            raise CatalogError(f"active profile not in catalog: {self._active}")

    @property
    def catalog(self) -> Catalog:
        return self._catalog

    def active(self) -> ModelProfile:
        return self._catalog.get(self._active)

    def set_active(self, profile_id: str) -> ModelProfile:
        profile = self._catalog.get(profile_id)
        self._active = profile.id
        return profile

    def profiles(self) -> list[ModelProfile]:
        return self._catalog.profiles()

    def route(self, purpose: str) -> Route:
        return self._policy.route(purpose)

    def complete(self, request: LLMRequest) -> LLMResponse:
        route = self._policy.route(request.purpose)
        last_error: LingquError | MissingCredential | None = None
        for index, profile in enumerate(route.candidates):
            try:
                credential = self._resolver.resolve(profile)
            except MissingCredential as exc:
                last_error = exc
                self.telemetry.record(
                    profile_id=profile.id,
                    purpose=request.purpose,
                    status="missing_credential",
                    latency_ms=0,
                    error_kind="missing_credential",
                    fallback_index=index,
                )
                continue
            model_request = ModelRequest(
                purpose=request.purpose,
                system=request.system,
                user=request.user,
                tools=request.tool_schemas,
                max_tokens=min(request.max_chars, 4096),
                temperature=request.temperature,
            )
            started = time.monotonic()
            try:
                reply = self._engine.complete(profile, model_request, credential)
            except Exception as exc:  # noqa: BLE001 - normalized below
                error = exc if isinstance(exc, LingquError) else _classify_unknown(exc)
                last_error = error
                self.telemetry.record(
                    profile_id=profile.id,
                    purpose=request.purpose,
                    status="error",
                    latency_ms=int((time.monotonic() - started) * 1000),
                    error_kind=error.kind,
                    fallback_index=index,
                )
                if should_fallback(error) and index < len(route.candidates) - 1:
                    continue
                raise _to_port_error(error)
            self.telemetry.record(
                profile_id=profile.id,
                purpose=request.purpose,
                status="ok",
                latency_ms=int((time.monotonic() - started) * 1000),
                usage=reply.usage,
                fallback_index=index,
            )
            return _to_llm_response(reply)
        if isinstance(last_error, MissingCredential):
            raise PortUnavailable("llm", str(last_error))
        if isinstance(last_error, LingquError):
            raise _to_port_error(last_error)
        raise PortUnavailable("llm", "no usable model profile")

    def probe(self, profile_id: str | None = None, *, timeout_s: float = 30.0) -> Health:
        profile = self._catalog.get(profile_id or self._active)
        started = time.monotonic()
        try:
            credential = self._resolver.resolve(profile)
        except MissingCredential as exc:
            return Health(profile_id=profile.id, state=HealthState.UNAVAILABLE, detail=str(exc))
        request = ModelRequest(
            purpose="muse",
            system="你是模型连通性探针，只回复 OK。",
            user="回复 OK",
            max_tokens=64,
            temperature=0.0,
        )
        try:
            reply = self._engine.complete(profile, request, credential)
        except LingquError as exc:
            return Health(profile_id=profile.id, state=HealthState.DEGRADED, detail=f"{exc.kind}: {exc.detail}", latency_ms=int((time.monotonic() - started) * 1000))
        except Exception as exc:  # noqa: BLE001
            return Health(profile_id=profile.id, state=HealthState.DEGRADED, detail=f"probe failed: {type(exc).__name__}", latency_ms=int((time.monotonic() - started) * 1000))
        return Health(profile_id=profile.id, state=HealthState.OK, detail=reply.text[:80], latency_ms=int((time.monotonic() - started) * 1000))

    def doctor(self) -> list[dict[str, str]]:
        return [self._resolver.describe(profile) for profile in self._catalog.profiles()]


def _classify_unknown(exc: Exception) -> LingquError:
    from .errors import classify

    return classify(exc)


def _to_port_error(error: LingquError) -> PortError:
    if isinstance(error, ProviderTimeout):
        return PortTimeout("llm", f"{error.kind}: {error.detail}")
    return PortUnavailable("llm", f"{error.kind}: {error.detail}")


def _to_llm_response(reply: ModelReply) -> LLMResponse:
    calls = tuple(
        ToolCall(name=call.name, arguments={str(k): str(v) for k, v in call.arguments.items()})
        for call in reply.tool_calls
    )
    return LLMResponse(text=reply.text, tool_calls=calls, finish=reply.finish)
