"""In-process model telemetry. Never stores prompts, keys or message bodies.

Counts and identities only: profile, purpose, status, latency, token usage and
error kind. A real deployment can ship this to Sentry/metrics; tests assert what
must not appear.
"""

from __future__ import annotations

import threading
import time
from dataclasses import asdict, dataclass
from typing import Any

from .contracts import Usage


@dataclass
class CallRecord:
    profile_id: str
    purpose: str
    status: str
    latency_ms: int
    prompt_tokens: int = 0
    completion_tokens: int = 0
    error_kind: str = ""
    fallback_index: int = 0


class Telemetry:
    def __init__(self, max_records: int = 500) -> None:
        self._max = max_records
        self._records: list[CallRecord] = []
        self._lock = threading.Lock()

    def record(
        self,
        *,
        profile_id: str,
        purpose: str,
        status: str,
        latency_ms: int,
        usage: Usage | None = None,
        error_kind: str = "",
        fallback_index: int = 0,
    ) -> CallRecord:
        item = CallRecord(
            profile_id=profile_id,
            purpose=purpose,
            status=status,
            latency_ms=max(0, int(latency_ms)),
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            error_kind=error_kind,
            fallback_index=fallback_index,
        )
        with self._lock:
            self._records.append(item)
            if len(self._records) > self._max:
                del self._records[: len(self._records) - self._max]
        return item

    def snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return [asdict(item) for item in self._records]

    def reset(self) -> None:
        with self._lock:
            self._records.clear()


class Timer:
    def __enter__(self) -> Timer:
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.monotonic() - self._start) * 1000)

    ms: int = 0
