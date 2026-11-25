"""Deterministic fakes: every test runs without network, clock or database server.

Fakes are ports, not shortcuts: they enforce the same return types, they can be
told to fail, and they never invent data the production adapter could not return.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from datetime import date, datetime

from harness.contracts import (
    EventHit,
    Hit,
    LLMRequest,
    LLMResponse,
    SearchQuery,
    ToolCall,
)
from ports import PortTimeout, PortUnavailable


class FakeClock:
    def __init__(self, now: datetime) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now

    def advance(self, seconds: int) -> None:
        from datetime import timedelta

        self._now = self._now + timedelta(seconds=seconds)


class FakeLLM:
    """Scripted completions. ``fail=True`` exercises the LLM-down branch."""

    def __init__(self, replies: Iterable[str] = (), *, fail: bool = False, tool_calls: Sequence[ToolCall] = ()) -> None:
        self._replies = list(replies)
        self.fail = fail
        self.tool_calls = tuple(tool_calls)
        self.calls: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.calls.append(request)
        if self.fail:
            raise PortTimeout("llm", "scripted timeout")
        if self.tool_calls and len(self.calls) == 1:
            return LLMResponse(text="", tool_calls=self.tool_calls, finish="tool_calls")
        text = self._replies.pop(0) if self._replies else "我在这里，慢慢说。"
        return LLMResponse(text=text, finish="stop")


def _hit(
    evidence_id: str,
    title: str,
    *,
    kind: str = "content",
    channel: str = "keyword",
    source_kind: str = "original",
    score: float = 1.0,
) -> Hit:
    return Hit(
        cite_as="S1",
        evidence_id=evidence_id,
        kind=kind,  # type: ignore[arg-type]
        title=title,
        content=f"{title} 的原文片段",
        score=score,
        channel=channel,  # type: ignore[arg-type]
        source_kind=source_kind,  # type: ignore[arg-type]
    )


class FakeSearch:
    """Keyword/dense/sparse over a shared corpus, with per-channel failure switches."""

    def __init__(self, corpus: Sequence[Hit] | None = None, *, down: Sequence[str] = ()) -> None:
        self.corpus = list(corpus or [])
        self.down = set(down)
        self.calls: list[tuple[str, str]] = []

    def _pick(self, channel: str, query: str) -> list[Hit]:
        self.calls.append((channel, query))
        if channel in self.down:
            raise PortUnavailable("search", f"{channel} down")
        return [h.model_copy(update={"channel": channel}) for h in self.corpus]

    def keyword(self, query: SearchQuery, *, alternates: Sequence[str] = ()) -> list[Hit]:
        return self._pick("keyword", query.query)

    def dense(self, query: SearchQuery) -> list[Hit]:
        return self._pick("dense", query.query)

    def sparse(self, query: SearchQuery) -> list[Hit]:
        return self._pick("sparse", query.query)

    def window(self, query: SearchQuery) -> list[Hit]:
        return self._pick("time", query.query)


class FakeEvents:
    def __init__(self, hits: Sequence[EventHit] = (), *, fail: bool = False) -> None:
        self._hits = list(hits)
        self.fail = fail
        self.calls = 0

    def hits(self, person_id: str, *, from_: date | None, to: date | None, keywords: Sequence[str]) -> list[EventHit]:
        self.calls += 1
        if self.fail:
            raise PortUnavailable("event", "event store offline")
        return list(self._hits)


class FakeTool:
    """Read-only tool double used by Muse's bounded tool loop."""

    def __init__(self, content: str = "检索到的原文", *, fail: bool = False) -> None:
        self.content = content
        self.fail = fail
        self.calls = 0

    def __call__(self, arguments: dict[str, str]):
        from harness.contracts import ToolResult

        self.calls += 1
        if self.fail:
            return ToolResult(name="search", ok=False, reason="scripted_error")
        return ToolResult(name="search", ok=True, content=self.content)
