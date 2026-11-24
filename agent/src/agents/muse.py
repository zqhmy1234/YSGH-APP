"""Muse: the only agent that speaks, and the only one with no write access.

Inner loop is bounded: at most two model calls, at most three tool calls, and
the tool set is read-only. A reply that fails validation is never returned as a
successful answer; the orchestrator turns it into a degraded reply.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping

from harness.contracts import Frame, LLMRequest, LLMResponse, ToolResult
from harness.obs import CostMeter
from ports import LLMPort, PortError

MAX_REPLY_CHARS = 1200
MAX_TOOL_CALLS = 3
MAX_STEPS = 2
_CITE = re.compile(r"\[(S\d{1,2}|W\d{1,2})\]")
_CJK = re.compile(r"[\u4e00-\u9fff]")
_LATIN = re.compile(r"[A-Za-z]{4,}")
_STATE_BLOCK_KEYS = frozenset({"tone", "familiarity_delta", "arc", "flags"})


def strip_state_block(text: str) -> str:
    """Remove the trailing Harness state block from a model reply.

    The output contract asks the model to end with
    ``{"tone":"gentle","familiarity_delta":0,"arc":"讲述中","flags":[]}``.
    That block is protocol metadata, not something the user should see, and
    its Latin keys would otherwise trip the Chinese-only reply validator and
    turn every real turn into a degraded reply. Only a trailing, parseable
    object whose keys are a subset of the state-block keys is removed.
    """
    candidate = text.rstrip()
    if not candidate.endswith("}"):
        return text
    start = candidate.rfind("{")
    if start == -1:
        return text
    import json

    try:
        parsed = json.loads(candidate[start:])
    except json.JSONDecodeError:
        return text
    if not isinstance(parsed, dict) or not parsed or not set(parsed).issubset(_STATE_BLOCK_KEYS):
        return text
    return candidate[:start].rstrip()

ToolFn = Callable[[dict[str, str]], ToolResult]


class Muse:
    def __init__(
        self,
        llm: LLMPort,
        tools: Mapping[str, ToolFn] | None = None,
        *,
        system_prefix: str = "",
        tool_schemas: tuple[dict, ...] = (),
    ) -> None:
        self._llm = llm
        self._tools = dict(tools or {})
        self._system_prefix = system_prefix.strip()
        self._tool_schemas = tool_schemas
        self._assembler = None

    def set_system_prefix(self, text: str) -> None:
        self._system_prefix = text.strip()

    def set_context_assembler(self, assembler) -> None:
        """The composition root injects the single assembler; Muse never builds one."""
        self._assembler = assembler

    # -- validation ---------------------------------------------------
    @staticmethod
    def validate_reply(text: str, locale: str = "zh") -> list[str]:
        issues: list[str] = []
        if not text.strip():
            issues.append("empty")
        if len(text) > MAX_REPLY_CHARS:
            issues.append("too_long")
        if locale == "zh" and not _CJK.search(text):
            issues.append("wrong_language")
        if locale == "zh" and len(_LATIN.findall(text)) > 3:
            issues.append("wrong_language_latin")
        return issues

    @staticmethod
    def cites(text: str) -> tuple[str, ...]:
        seen: list[str] = []
        for m in _CITE.finditer(text):
            if m.group(1) not in seen:
                seen.append(m.group(1))
        return tuple(seen)

    # -- loop ---------------------------------------------------------
    def speak(
        self,
        context: Frame | object,
        *,
        user_text: str,
        locale: str = "zh",
        meter: CostMeter | None = None,
    ) -> tuple[str, tuple[str, ...]]:
        """Accept ``AssembledContext`` (preferred) or a bare ``Frame``.

        Everything with ``render()`` can be spoken to; no caller may pass an
        ad-hoc system prompt string. That is the §5.2 single-entry rule.
        """
        base = context.render() or "你是回忆陪伴者，用中文回答。"
        system = f"{self._system_prefix}\n\n## 本轮上下文\n{base}" if self._system_prefix else base
        if len(system) > 7900:
            room = max(200, 7900 - len(self._system_prefix) - len("\n\n## 本轮上下文\n"))
            system = f"{self._system_prefix}\n\n## 本轮上下文\n{base[:room]}"
        user = user_text
        text = ""
        for step in range(MAX_STEPS):
            request = LLMRequest(
                purpose="muse",
                system=system,
                user=user,
                tools=tuple(self._tools),
                tool_schemas=self._tool_schemas,
                max_chars=min(MAX_REPLY_CHARS, 400 + 200 * step),
            )
            try:
                response: LLMResponse = self._llm.complete(request)
            except PortError:
                raise
            if meter:
                meter.add_llm(len(response.text))
            if response.finish == "tool_calls" and response.tool_calls:
                observations = self._run_tools(response.tool_calls)
                user = f"{user_text}\n\n工具结果：\n{observations}\n请据此作答，不要编造。"
                continue
            text = strip_state_block(response.text)
            break
        issues = self.validate_reply(text, locale)
        if issues:
            raise PortError("muse", "invalid_reply:" + ",".join(issues))
        return text.strip(), self.cites(text)

    def _run_tools(self, calls) -> str:
        lines: list[str] = []
        for call in list(calls)[:MAX_TOOL_CALLS]:
            fn = self._tools.get(call.name)
            if fn is None:
                lines.append(f"{call.name}: unavailable")
                continue
            try:
                result = fn(dict(call.arguments))
                lines.append(f"{call.name}: {result.content[:400]}")
            except Exception as exc:  # a tool failure is an observation, not a crash
                lines.append(f"{call.name}: error:{type(exc).__name__}")
        return "\n".join(lines)
