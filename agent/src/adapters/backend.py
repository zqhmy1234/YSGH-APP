"""Production adapters: the only place allowed to speak HTTP.

Every adapter maps transport errors onto the three port errors, so callers never
see ``HTTPError``/``TimeoutError``. A missing configuration is ``PortUnavailable``
(an honest gap), never a silent empty result.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import date

from harness.contracts import EventHit, Hit, LLMRequest, LLMResponse, SearchQuery, ToolCall
from ports import PortTimeout, PortUnavailable


def _post(url: str, payload: dict, *, timeout_s: float, headers: dict[str, str] | None = None) -> dict:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except TimeoutError:
        raise PortTimeout("http", f"timeout after {timeout_s}s")
    except urllib.error.HTTPError as exc:
        raise PortUnavailable("http", f"http {exc.code}")
    except urllib.error.URLError as exc:
        raise PortUnavailable("http", f"unreachable: {exc.reason}")


class BackendLLM:
    """OpenAI-compatible chat endpoint. Temperature and caps come from the contract."""

    def __init__(
        self, base_url: str = "", api_key: str = "", model: str = "qwen-flash", timeout_s: float = 3.0
    ) -> None:
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._model = model
        self._timeout = timeout_s

    def complete(self, request: LLMRequest) -> LLMResponse:
        if not self._base:
            raise PortUnavailable("llm", "LLM_BASE_URL not configured")
        payload = {
            "model": self._model,
            "messages": [{"role": "system", "content": request.system}, {"role": "user", "content": request.user}],
            "temperature": request.temperature,
            "max_tokens": request.max_chars,
        }
        data = _post(
            f"{self._base}/chat/completions",
            payload,
            timeout_s=self._timeout,
            headers={"Authorization": f"Bearer {self._key}"},
        )
        message = (data.get("choices") or [{}])[0].get("message", {})
        calls = tuple(
            ToolCall(
                name=c.get("function", {}).get("name", ""),
                arguments=json.loads(c.get("function", {}).get("arguments") or "{}"),
            )
            for c in message.get("tool_calls") or []
        )
        return LLMResponse(
            text=message.get("content") or "", tool_calls=calls, finish="tool_calls" if calls else "stop"
        )


class BackendSearch:
    """Thin client over the backend RAG facade; one method per named vector."""

    def __init__(self, base_url: str = "", token: str = "", timeout_s: float = 0.8) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    def _search(self, channel: str, query: SearchQuery) -> list[Hit]:
        if not self._base:
            raise PortUnavailable("search", "SEARCH_BASE_URL not configured")
        payload = {
            "query": query.query,
            "person_id": query.person_id,
            "channel": channel,
            "limit": query.limit,
            "layers": [layer.value for layer in query.layers],
        }
        data = _post(
            f"{self._base}/internal/rag/search",
            payload,
            timeout_s=self._timeout,
            headers={"X-Internal-Token": self._token},
        )
        return [_to_hit(item, channel) for item in data.get("items") or []]

    def keyword(self, query: SearchQuery, *, alternates: Sequence[str] = ()) -> list[Hit]:
        out: list[Hit] = []
        for term in (query.query, *alternates):
            out.extend(self._search("keyword", query.model_copy(update={"query": term})))
        return out

    def dense(self, query: SearchQuery) -> list[Hit]:
        return self._search("dense", query)

    def sparse(self, query: SearchQuery) -> list[Hit]:
        return self._search("sparse", query)

    def window(self, query: SearchQuery) -> list[Hit]:
        payload_query = query.model_copy(update={"query": query.query})
        return self._search("time", payload_query)


class BackendEvents:
    def __init__(self, base_url: str = "", token: str = "", timeout_s: float = 0.8) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._timeout = timeout_s

    def hits(self, person_id: str, *, from_: date | None, to: date | None, keywords: Sequence[str]) -> list[EventHit]:
        if not self._base:
            raise PortUnavailable("event", "EVENT_BASE_URL not configured")
        payload = {
            "person_id": person_id,
            "from": from_.isoformat() if from_ else None,
            "to": to.isoformat() if to else None,
            "keywords": list(keywords),
        }
        data = _post(
            f"{self._base}/internal/events/hits",
            payload,
            timeout_s=self._timeout,
            headers={"X-Internal-Token": self._token},
        )
        return [EventHit(**item) for item in data.get("items") or []]


def _to_hit(item: dict, channel: str) -> Hit:
    return Hit(
        cite_as="S1",
        evidence_id=str(item.get("evidence_id") or item.get("id") or ""),
        kind=item.get("kind", "content"),
        title=str(item.get("title") or "未命名"),
        time_hint=str(item.get("time_hint") or ""),
        content=str(item.get("content") or ""),
        score=float(item.get("score") or 0.0),
        channel=channel,
        source_kind=item.get("source_kind", "original"),
    )
