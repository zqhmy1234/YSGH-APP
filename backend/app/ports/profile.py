"""ProfileDigestPort: read-only 8-section projection, ≤600 chars, three states."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable

DigestStatus = Literal["ok", "insufficient_data", "unavailable"]


@dataclass(frozen=True)
class DigestSection:
    name: str
    text: str


@dataclass(frozen=True)
class ProfileDigest:
    status: DigestStatus
    sections: tuple[DigestSection, ...] = ()
    text: str = ""
    chars: int = 0


@runtime_checkable
class ProfileDigestPort(Protocol):
    def digest(self, user_id: str) -> ProfileDigest: ...
