"""Catalog of model profiles: versioned JSON, validated at load time.

A profile is configuration, not code. Adding a provider means adding a row and
pointing LiteLLM at it; the gateway stays provider-agnostic.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .contracts import ModelProfile, ProviderId

DEFAULT_CATALOG = Path(__file__).resolve().parents[3] / "config" / "lingqu-catalog.json"
REQUIRED_PROVIDERS = frozenset({item.value for item in ProviderId})


class CatalogError(ValueError):
    pass


class Catalog:
    def __init__(self, profiles: list[ModelProfile], *, default_profile: str, catalog_version: str, verified_at: str, source_path: str = "") -> None:
        if len(profiles) != len({p.id for p in profiles}):
            raise CatalogError("duplicate profile id")
        self._profiles = {p.id: p for p in profiles}
        self.default_profile = default_profile
        self.catalog_version = catalog_version
        self.verified_at = verified_at
        self.source_path = source_path
        self._validate()

    def _validate(self) -> None:
        providers = {p.provider.value for p in self._profiles.values()}
        missing = REQUIRED_PROVIDERS - providers
        if missing:
            raise CatalogError(f"missing providers: {sorted(missing)}")
        if self.default_profile not in self._profiles:
            raise CatalogError(f"default profile not in catalog: {self.default_profile}")
        if self._profiles[self.default_profile].provider is not ProviderId.DEEPSEEK:
            raise CatalogError("default profile must be DeepSeek")
        for profile in self._profiles.values():
            if not profile.source:
                raise CatalogError(f"{profile.id}: source is required")
            if not profile.verified_at:
                raise CatalogError(f"{profile.id}: verified_at is required")

    def profiles(self) -> list[ModelProfile]:
        return list(self._profiles.values())

    def get(self, profile_id: str) -> ModelProfile:
        try:
            return self._profiles[profile_id]
        except KeyError as exc:
            raise CatalogError(f"unknown profile: {profile_id}") from exc

    def has(self, profile_id: str) -> bool:
        return profile_id in self._profiles

    def by_provider(self, provider: ProviderId | str) -> list[ModelProfile]:
        wanted = ProviderId(provider)
        return [p for p in self._profiles.values() if p.provider is wanted]

    def default(self) -> ModelProfile:
        return self._profiles[self.default_profile]

    def ids(self) -> tuple[str, ...]:
        return tuple(self._profiles)


def load_catalog(path: str | Path | None = None) -> Catalog:
    catalog_path = Path(path or os.environ.get("LINGQU_CATALOG_PATH", DEFAULT_CATALOG))
    try:
        raw: dict[str, Any] = json.loads(catalog_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CatalogError(f"catalog not found: {catalog_path}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"catalog is not valid JSON: {catalog_path}") from exc

    try:
        raw_profiles = raw["profiles"]
        default = str(raw["default_profile"])
        version = str(raw["catalog_version"])
        verified = str(raw["verified_at"])
        for item in raw_profiles:
            item.setdefault("verified_at", verified)
        profiles = [ModelProfile.model_validate(item) for item in raw_profiles]
    except (KeyError, TypeError) as exc:
        raise CatalogError(f"catalog missing required key: {exc}") from exc

    return Catalog(profiles, default_profile=default, catalog_version=version, verified_at=verified, source_path=str(catalog_path))
