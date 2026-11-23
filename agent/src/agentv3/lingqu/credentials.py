"""Credential resolution with two supported shapes.

1. One company gateway key: ``COMPANY_LLM_API_KEY`` (+ optional
   ``COMPANY_LLM_BASE_URL``), shared by all profiles.
2. Per-provider keys: ``LINGQU_DEEPSEEK_API_KEY`` ... override the company key.

Credentials never enter the catalog or logs. ``Credential`` has a redacted
``__repr__`` and ``fingerprint`` is a sha256 prefix, not a key fragment.
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from typing import Mapping

from .contracts import ModelProfile


class MissingCredential(RuntimeError):
    """No usable key for a profile; this is an honest gap, not an auth failure."""


@dataclass(frozen=True)
class Credential:
    api_key: str = field(repr=False)
    base_url: str = ""
    source: str = ""
    gateway: bool = False

    @property
    def fingerprint(self) -> str:
        return hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()[:8]

    def __repr__(self) -> str:
        return f"Credential(source={self.source!r}, fingerprint={self.fingerprint!r}, base_url={self.base_url!r})"


_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._\-]{8,}"),
    re.compile(r"(?i)sk-[a-z0-9._\-]{8,}"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*[^\s,}]+"),
)


def redact(text: str) -> str:
    out = text
    for pattern in _SECRET_PATTERNS:
        out = pattern.sub("[REDACTED]", out)
    return out


class SecretResolver:
    def __init__(self, env: Mapping[str, str] | None = None) -> None:
        self._env = dict(os.environ if env is None else env)

    def _get(self, name: str) -> str:
        return (self._env.get(name) or "").strip()

    def _base_url(self, profile: ModelProfile) -> str:
        provider_base = self._get(f"LINGQU_{profile.provider.value.upper()}_BASE_URL")
        company_base = self._get("COMPANY_LLM_BASE_URL")
        raw = provider_base or company_base or profile.base_url
        if "{" in raw:
            workspace = (
                self._get(f"LINGQU_{profile.provider.value.upper()}_WORKSPACE_ID")
                or self._get("COMPANY_LLM_WORKSPACE_ID")
                or self._get("QWEN_WORKSPACE_ID")
            )
            if not workspace:
                raw = profile.base_url_fallback
            else:
                raw = raw.format(workspace_id=workspace)
        return raw.rstrip("/")

    def resolve(self, profile: ModelProfile) -> Credential:
        provider_key = self._get(f"LINGQU_{profile.provider.value.upper()}_API_KEY")
        company_key = self._get("COMPANY_LLM_API_KEY")
        key = provider_key or company_key
        if not key:
            raise MissingCredential(
                f"no credential for {profile.id}; set COMPANY_LLM_API_KEY or "
                f"LINGQU_{profile.provider.value.upper()}_API_KEY"
            )
        source = f"provider:{profile.provider.value}" if provider_key else "company_llm"
        company_base = self._get("COMPANY_LLM_BASE_URL")
        provider_base = self._get(f"LINGQU_{profile.provider.value.upper()}_BASE_URL")
        # A company gateway is a generic OpenAI-compatible endpoint. The engine
        # must use the ``openai/`` prefix for it; a provider prefix would bypass
        # the gateway and call the provider directly with the gateway key.
        gateway = bool(company_base and not provider_base and not provider_key)
        return Credential(api_key=key, base_url=self._base_url(profile), source=source, gateway=gateway)

    def describe(self, profile: ModelProfile) -> dict[str, str]:
        try:
            credential = self.resolve(profile)
        except MissingCredential:
            return {"profile": profile.id, "credential": "missing", "base_url": self._base_url(profile)}
        return {
            "profile": profile.id,
            "credential": credential.source,
            "fingerprint": credential.fingerprint,
            "base_url": credential.base_url,
        }
