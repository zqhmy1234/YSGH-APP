"""Read-only model administration, shared by HTTP and CLI.

The HTTP layer must never serialize a credential. This module exposes only
profile ids, capabilities, sources and masked fingerprints.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .contracts import Health, ModelProfile
from .gateway import LingquGateway


def profile_info(gateway: LingquGateway, profile: ModelProfile, *, active: str) -> dict[str, Any]:
    try:
        credential = gateway._resolver.resolve(profile)  # noqa: SLF001 - admin is the resolver's owner
        credential_state = credential.source
        fingerprint = credential.fingerprint
        base_url = credential.base_url
    except Exception:  # noqa: BLE001 - missing/misconfigured credential is a display state
        credential_state = "missing"
        fingerprint = ""
        base_url = profile.base_url
    return {
        "id": profile.id,
        "provider": profile.provider.value,
        "model": profile.model,
        "base_url": base_url,
        "api_mode": profile.api_mode.value,
        "context_window": profile.context_window,
        "max_output": profile.max_output,
        "capabilities": list(profile.capabilities),
        "is_default": profile.id == gateway.catalog.default_profile,
        "is_active": profile.id == active,
        "credential": credential_state,
        "fingerprint": fingerprint,
        "source": profile.source,
        "verified_at": profile.verified_at,
    }


def list_models(gateway: LingquGateway) -> dict[str, Any]:
    active = gateway.active().id
    return {
        "catalog_version": gateway.catalog.catalog_version,
        "verified_at": gateway.catalog.verified_at,
        "default_profile": gateway.catalog.default_profile,
        "active_profile": active,
        "profiles": [profile_info(gateway, p, active=active) for p in gateway.profiles()],
    }


def doctor(gateway: LingquGateway) -> list[dict[str, str]]:
    return gateway.doctor()


def probe(gateway: LingquGateway, profile_id: str | None = None) -> Health:
    return gateway.probe(profile_id)


class SelectionStore:
    """Optional JSON persistence for the active profile.

    A fresh deployment with no file starts from LINGQU_DEFAULT_PROFILE. The file
    contains only the profile id, never a credential.
    """

    def __init__(self, path: str | None = None) -> None:
        self._path = Path(path or os.environ.get("LINGQU_SELECTION_PATH", "")) if (path or os.environ.get("LINGQU_SELECTION_PATH")) else None

    def load(self, default: str) -> str:
        if not self._path or not self._path.exists():
            return default
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            return str(data.get("active_profile") or default)
        except (OSError, ValueError, TypeError):
            return default

    def save(self, profile_id: str) -> None:
        if not self._path:
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps({"active_profile": profile_id}, ensure_ascii=False), encoding="utf-8")
