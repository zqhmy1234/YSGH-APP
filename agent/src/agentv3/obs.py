"""Observation: spans, cost, degradation counters and log redaction.

Ported behaviour from ``agent/src/yishu`` (observability + degradation): every
degrade is counted and exposed instead of being logged and forgotten.
"""

from __future__ import annotations

import re
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, Iterator

_SENSITIVE_KEYS = ("token", "secret", "password", "authorization", "cookie", "key")
_PII = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")  # CN mobile


def redact(value: Any, depth: int = 0) -> Any:
    """Never let a credential or a phone number reach a log line."""
    if depth > 4:
        return "…"
    if isinstance(value, dict):
        return {
            k: ("***" if any(s in k.lower() for s in _SENSITIVE_KEYS) else redact(v, depth + 1))
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [redact(v, depth + 1) for v in value]
    if isinstance(value, str):
        return _PII.sub("***", value)
    return value


@dataclass
class Span:
    name: str
    fields: dict[str, Any] = field(default_factory=dict)
    ms: int = 0

    def __enter__(self) -> "Span":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)


@dataclass
class Tracer:
    """In-memory span sink. Production adapters forward the same records."""

    spans: list[dict[str, Any]] = field(default_factory=list)

    def span(self, name: str, **fields: str | int | float | bool) -> "_TracingSpan":
        return _TracingSpan(name=name, sink=self.spans, fields=dict(fields))

    def total_ms(self) -> int:
        return sum(int(s["ms"]) for s in self.spans)


class _TracingSpan:
    """Context manager that records itself on exit (stdlib timing)."""

    def __init__(self, name: str, sink: list[dict[str, Any]], fields: dict[str, Any]) -> None:
        self.name = name
        self._sink = sink
        self._fields = fields
        self.ms = 0

    def __enter__(self) -> "_TracingSpan":
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> None:
        self.ms = int((time.perf_counter() - self._t0) * 1000)
        self._sink.append({"name": self.name, "ms": self.ms, **redact(self._fields)})


@dataclass
class Degradation:
    """Counts by reason; ``total`` > 0 means an enhancement path is failing."""

    counts: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    def note(self, reason: str) -> None:
        self.counts[reason] += 1

    def snapshot(self) -> dict[str, int]:
        return {"total": sum(self.counts.values()), **dict(self.counts)}

    def reasons(self) -> tuple[str, ...]:
        return tuple(sorted(self.counts))

    def reset(self) -> None:
        self.counts.clear()


@dataclass
class CostMeter:
    llm_calls: int = 0
    chars: int = 0
    started: float = field(default_factory=time.perf_counter)

    def add_llm(self, chars: int) -> None:
        self.llm_calls += 1
        self.chars += chars

    @property
    def ms(self) -> int:
        return int((time.perf_counter() - self.started) * 1000)


def iter_ports(*names: str) -> Iterator[str]:
    yield from names
