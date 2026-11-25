"""The remaining L2 protocols: timeline/ingest/consent/vault/lifecycle/export/persona/eval.

They are declared here as one frozen contract file so the 15-port count has a
single place to audit. Implementations appear only when the corresponding
domain task lands; calling an unimplemented port is an explicit
``PortUnavailable``, never a fake success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


class PortUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class TimelinePage:
    status: Literal["ok", "empty", "unavailable"]
    items: tuple[dict, ...] = ()
    next_cursor: str = ""


@runtime_checkable
class TimelinePort(Protocol):
    def page(self, user_id: str, *, cursor: str = "", limit: int = 20) -> TimelinePage: ...


@dataclass(frozen=True)
class IngestJob:
    job_id: str
    status: Literal["queued", "running", "partial", "done", "failed"]
    total: int = 0
    done: int = 0


@runtime_checkable
class IngestPort(Protocol):
    def enqueue(self, user_id: str, items: list[dict]) -> IngestJob: ...

    def progress(self, job_id: str) -> IngestJob: ...


@runtime_checkable
class ConsentPort(Protocol):
    def get(self, user_id: str, scope: str) -> Literal["ok", "not_granted", "revoked"]: ...

    def set(self, user_id: str, scope: str, granted: bool) -> None: ...


@runtime_checkable
class VaultPort(Protocol):
    def lock(self, user_id: str) -> None: ...

    def unlock(self, user_id: str, key: str) -> Literal["ok", "wrong_key", "unavailable"]: ...


@runtime_checkable
class LifecyclePort(Protocol):
    def request_delete(self, user_id: str) -> str: ...

    def purge_status(self, request_id: str) -> Literal["scheduled", "purging", "done", "failed"]: ...


@dataclass(frozen=True)
class ExportPage:
    status: Literal["ok", "truncated", "failed"]
    cursor: str = ""
    items: tuple[dict, ...] = field(default_factory=tuple)


@runtime_checkable
class ExportPort(Protocol):
    def page(self, user_id: str, *, cursor: str = "") -> ExportPage: ...


@runtime_checkable
class PersonaPort(Protocol):
    def snapshot(self, user_id: str) -> dict[str, Any]: ...

    def set_active(self, user_id: str, role_id: str) -> int: ...


@runtime_checkable
class EvalPort(Protocol):
    def run(self, dataset: str) -> Literal["pass", "warn", "fail", "unavailable"]: ...
