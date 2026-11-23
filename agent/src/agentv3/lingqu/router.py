"""Routing policy: explicit profile choice plus provider-failure fallback.

Deliberately *not* semantic routing: a turn does not silently switch to a
different model because the text looked a certain way. The only automatic switch
is failure-driven (429/5xx/timeout), and it is recorded in telemetry.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping

from .catalog import Catalog, CatalogError
from .contracts import ModelProfile


class RoutingConfigError(ValueError):
    pass


@dataclass(frozen=True)
class Route:
    purpose: str
    primary: ModelProfile
    candidates: tuple[ModelProfile, ...]
    fallback_enabled: bool


_DEFAULT_FALLBACK = "qwen/qwen4-max,glm/glm-5.3"


class RoutingPolicy:
    def __init__(self, catalog: Catalog, env: Mapping[str, str] | None = None) -> None:
        self._catalog = catalog
        self._env = dict(os.environ if env is None else env)

    def _get(self, name: str) -> str:
        return (self._env.get(name) or "").strip()

    def primary_for(self, purpose: str) -> ModelProfile:
        env_name = f"LINGQU_PROFILE_{purpose.upper()}"
        wanted = self._get(env_name)
        if wanted:
            if not self._catalog.has(wanted):
                raise RoutingConfigError(f"{env_name}={wanted!r} is not in the catalog")
            return self._catalog.get(wanted)
        return self._catalog.default()

    def _fallback_profiles(self, primary: ModelProfile) -> tuple[ModelProfile, ...]:
        if self._get("LINGQU_FALLBACK_ENABLED") in {"0", "false", "False"}:
            return ()
        raw = self._get("LINGQU_FALLBACK_CHAIN") or _DEFAULT_FALLBACK
        out: list[ModelProfile] = []
        for item in (part.strip() for part in raw.split(",")):
            if not item or item == primary.id:
                continue
            if not self._catalog.has(item):
                raise RoutingConfigError(f"LINGQU_FALLBACK_CHAIN contains unknown profile {item!r}")
            out.append(self._catalog.get(item))
        return tuple(out)

    def route(self, purpose: str) -> Route:
        primary = self.primary_for(purpose)
        candidates = (primary, *self._fallback_profiles(primary))
        return Route(purpose=purpose, primary=primary, candidates=candidates, fallback_enabled=len(candidates) > 1)

    def all_routes(self) -> dict[str, str]:
        purposes = ("muse", "librarian_rewrite", "provenance", "sculptor", "digest")
        return {purpose: self.primary_for(purpose).id for purpose in purposes}


__all__ = ["Route", "RoutingConfigError", "RoutingPolicy", "CatalogError"]
