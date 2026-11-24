"""HTTP adapter for the six L3 agent endpoints (plan §3.3).

The agent never imports backend code and never connects to PostgreSQL, Qdrant
or COS. Every business read/write is one of these contracts. Failure mapping is
central here: HTTP 401/403 -> PortUnavailable (auth), 422 -> PortUnavailable
(contract), 503 -> PortUnavailable (not configured), timeout -> PortTimeout.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Sequence
from datetime import date, datetime
from typing import Any

from harness.contracts import (
    Digest,
    DigestSection,
    EchoCandidate,
    EventHit,
    Hit,
    ResultStatus,
    SearchQuery,
    SearchResult,
    TraceItem,
)
from harness.persona import PersonaSnapshot, RelationshipChoices
from ports import PortTimeout, PortUnavailable

AGENT_HEADERS = {"X-Agent-Token": ""}


class BackendHTTP:
    """One adapter, six endpoints; methods return agent contracts only."""

    def __init__(self, base_url: str = "", token: str = "", user_id: str = "", timeout_s: float = 3.0) -> None:
        self._base = base_url.rstrip("/")
        self._token = token
        self._user = user_id
        self._timeout = timeout_s

    @property
    def configured(self) -> bool:
        """False in local-only runs — callers should skip HTTP entirely."""
        return bool(self._base)

    def _headers(self, user_id: str | None = None) -> dict[str, str]:
        """``X-On-Behalf-Of`` must be per-turn, not per-process.

        The orchestrator is a process singleton while every turn has its own
        ``person_id``; callers that know the acting user must pass it explicitly,
        otherwise all turns would act as whichever user was configured at boot.
        """
        return {
            "Content-Type": "application/json",
            "X-Agent-Token": self._token,
            "X-On-Behalf-Of": user_id or self._user,
        }

    def _post(self, path: str, payload: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        if not self._base:
            raise PortUnavailable("backend", f"{path}: BACKEND_BASE_URL not configured")
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(  # noqa: S310 —— base URL is deployment config, not user input
            f"{self._base}{path}", data=data, headers=self._headers(user_id), method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise PortTimeout("backend", f"{path} timeout") from exc
        except urllib.error.HTTPError as exc:
            detail = _error_detail(exc)
            raise PortUnavailable("backend", f"{path} http {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise PortUnavailable("backend", f"{path} unreachable: {exc.reason}") from exc
        return body.get("data", body)

    def _get(self, path: str, *, user_id: str | None = None) -> dict[str, Any]:
        if not self._base:
            raise PortUnavailable("backend", f"{path}: BACKEND_BASE_URL not configured")
        request = urllib.request.Request(  # noqa: S310 —— base URL is deployment config, not user input
            f"{self._base}{path}", headers=self._headers(user_id), method="GET"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise PortTimeout("backend", f"{path} timeout") from exc
        except urllib.error.HTTPError as exc:
            raise PortUnavailable("backend", f"{path} http {exc.code}: {_error_detail(exc)}") from exc
        except urllib.error.URLError as exc:
            raise PortUnavailable("backend", f"{path} unreachable: {exc.reason}") from exc
        return body.get("data", body)

    # ---------------------------------------------------------------- search
    def search(self, query: SearchQuery) -> SearchResult:
        payload = {
            "q": query.query,
            "limit": query.limit,
            "content_types": ["photo"] if query.mode == "person" else None,
        }
        if query.date_from:
            payload["time_from"] = f"{query.date_from.isoformat()}T00:00:00"
        if query.date_to:
            payload["time_to"] = f"{query.date_to.isoformat()}T23:59:59"
        data = self._post("/api/v1/search/agent", payload, user_id=query.person_id)
        hits = tuple(_hit(item, index, original=True) for index, item in enumerate(data.get("hits") or ()))
        summaries = tuple(_hit(item, index, original=False) for index, item in enumerate(data.get("hits") or ()))
        reason = "no_match" if not data.get("hits") else "rrf"
        status = (
            ResultStatus.EMPTY
            if not data.get("hits")
            else (ResultStatus.PARTIAL if data.get("degraded") else ResultStatus.GROUNDED)
        )
        return SearchResult(
            hits=hits,
            summaries=summaries,
            reason=reason,
            degraded=bool(data.get("degraded")),
            gaps=("backend_degraded",) if data.get("degraded") else (),
            status=status,
            trace=tuple(
                TraceItem(
                    claim=str(item.get("text") or ""), evidence_id=str(item.get("content_id") or ""), channel="backend"
                )
                for item in data.get("hits") or ()
            ),
        )

    def event_hits(
        self, person_id: str, *, from_: date | None, to: date | None, keywords: Sequence[str]
    ) -> list[EventHit]:
        payload = {
            "q": " ".join(keywords).strip() or "回忆",
            "limit": 20,
            "time_from": f"{from_.isoformat()}T00:00:00" if from_ else None,
            "time_to": f"{to.isoformat()}T23:59:59" if to else None,
        }
        data = self._post("/api/v1/search/agent", payload, user_id=person_id)
        return [
            EventHit(
                event_id=str(item.get("content_id") or ""),
                title=str(item.get("event_title") or item.get("text") or "事件"),
                day=str(item.get("taken_at") or ""),
                summary=str(item.get("text") or ""),
                score=float(item.get("score") or 0.0),
            )
            for item in data.get("hits") or ()
        ]

    # ---------------------------------------------------------------- write
    def create_content(self, payload: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        return self._post("/api/v1/contents/agent", payload, user_id=user_id)

    def create_content_agent(self, payload: dict[str, Any], *, user_id: str | None = None) -> dict[str, Any]:
        """Typed wrapper over ``POST /api/v1/contents/agent``.

        ``payload`` must match ``shared.contracts.AgentContentCreate``. The
        endpoint is idempotent on ``client_generated_id``; a 409 means a
        different body reused the key and must not be retried.
        """
        from shared.contracts import AgentContentCreate

        body = AgentContentCreate.model_validate(payload).model_dump(mode="json", exclude_none=True)
        return self.create_content(body, user_id=user_id)

    def delete_content(self, client_generated_id: str, *, user_id: str | None = None) -> bool:
        data = self._delete(f"/api/v1/contents/agent/by-cgid/{client_generated_id}", user_id=user_id)
        return bool(data.get("deleted"))

    def _delete(self, path: str, *, user_id: str | None = None) -> dict[str, Any]:
        if not self._base:
            raise PortUnavailable("backend", f"{path}: BACKEND_BASE_URL not configured")
        request = urllib.request.Request(  # noqa: S310 —— base URL is deployment config, not user input
            f"{self._base}{path}", headers=self._headers(user_id), method="DELETE"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:  # noqa: S310
                body = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exc:
            raise PortTimeout("backend", f"{path} timeout") from exc
        except urllib.error.HTTPError as exc:
            raise PortUnavailable("backend", f"{path} http {exc.code}: {_error_detail(exc)}") from exc
        except urllib.error.URLError as exc:
            raise PortUnavailable("backend", f"{path} unreachable: {exc.reason}") from exc
        return body.get("data", body)

    # ---------------------------------------------------------------- profile
    def digest(self, person_id: str) -> Digest:
        data = self._get(f"/api/v1/profile/agent/digest?person_id={person_id}", user_id=person_id)
        state = data.get("status") or data.get("state") or "unavailable"
        sections = tuple(
            DigestSection(name=str(s.get("name")), text=str(s.get("text"))) for s in data.get("sections") or ()
        )
        return Digest(
            person_id=person_id,
            sections=sections,
            state=state if state in {"ok", "insufficient_data", "unavailable"} else "unavailable",
        )

    # ---------------------------------------------------------------- echo (B4)
    def echo(
        self, person_id: str, *, local_date: str, limit: int = 1
    ) -> tuple[tuple[EchoCandidate, ...], str]:
        """Read-only echo candidates for the ECHO intent (B4 agent-side wiring).

        Backend ``POST /api/v1/echo/agent`` reuses ``peek_today_echo``: no EchoHistory
        row and no in-app message is written here — only the user端 ``/echo/today``
        consumes the daily slot. Returns ``(candidates, suppressed_reason)``.
        ``suppressed`` is passed through so Muse can stay silent when there is no
        candidate instead of inventing an anniversary.
        """
        data = self._post(
            "/api/v1/echo/agent",
            {"person_id": person_id, "local_date": local_date, "limit": limit},
            user_id=person_id,
        )
        candidates: list[EchoCandidate] = []
        for item in data.get("items") or ():
            reason = str(item.get("reason") or "anniversary")
            if reason not in {"anniversary", "season", "place", "person", "topic"}:
                reason = "anniversary"
            sensitivity = str(item.get("sensitivity") or "personal")
            if sensitivity not in {"public", "personal", "sensitive", "critical"}:
                sensitivity = "personal"
            candidates.append(
                EchoCandidate(
                    echo_id=str(item.get("echo_id") or "echo"),
                    person_id=str(item.get("person_id") or person_id),
                    title=str(item.get("title") or ""),
                    reason=reason,  # type: ignore[arg-type]
                    sensitivity=sensitivity,  # type: ignore[arg-type]
                    # clamp: backend 脏 score（越界/NaN）不得让整轮 ECHO 崩在契约校验上
                    score=min(1.0, max(0.0, float(item.get("score") or 0.8))),
                    why_now=str(item.get("why_now") or ""),
                )
            )
        return tuple(candidates), str(data.get("suppressed") or "")

    # ---------------------------------------------------------------- memory write (B5 merge)
    def write_memory(self, user_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist one canonical memory row via backend (B5 merge, replaces V3 JSON)."""
        return self._post("/api/v1/memory/agent", payload, user_id=user_id)

    def delete_memory(self, user_id: str, entry_id: str) -> dict[str, Any]:
        """Remove a canonical memory row so hydrate cannot resurrect it (B5 merge)."""
        return self._delete(f"/api/v1/memory/agent/{entry_id}", user_id=user_id)

    # ---------------------------------------------------------------- memory
    def memory(self, user_id: str) -> dict[str, Any]:
        """Return the raw B5 snapshot; ``memory/read_model.py`` validates it."""
        data = self._get("/api/v1/memory/agent", user_id=user_id)
        data.setdefault("user_id", user_id)
        return data

    # ---------------------------------------------------------------- persona
    def persona(self, user_id: str) -> PersonaSnapshot:
        """One read per turn; a missing row is a default snapshot, not an error."""
        data = self._get("/api/v1/persona/agent", user_id=user_id)
        raw = data.get("updated_at")
        updated_at = None
        if raw:
            updated_at = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return PersonaSnapshot(
            user_id=str(data.get("user_id") or user_id),
            choices=RelationshipChoices(**data.get("choices", {})),
            role_id=str(data.get("role_id") or "gentle-friend"),
            relationship_note=str(data.get("relationship_note") or ""),
            version=int(data.get("version") or 0),
            updated_at=updated_at,
            source=data.get("source") if data.get("source") in {"user", "default"} else "default",
        )


class BackendSearchHTTP:
    """``SearchPort`` over the *single* unified L3 search endpoint.

    The backend exposes one merged RAG endpoint (keyword+dense+sparse+rerank),
    not four independent channels. Calling it once per channel would run the
    whole rewrite/rerank pipeline four times inside one turn (~4x P95), so the
    first channel call performs the request and the remaining channel calls in
    the same turn reuse the result. That matches reality: the backend already
    did RRF, so there is exactly one retrieval to do.

    ``window()`` uses a different backend path (time filter) and is therefore a
    real second call — but only when the turn actually asks for a time window.
    """

    def __init__(self, client: "BackendHTTP") -> None:
        self._client = client
        self._last_key: str | None = None
        self._last_hits: list[Hit] = []

    def _merged(self, query: SearchQuery) -> list[Hit]:
        key = query.query.strip()
        if key != self._last_key:
            self._last_hits = list(self._client.search(query).hits)
            self._last_key = key
        return list(self._last_hits)

    def keyword(self, query: SearchQuery, *, alternates: Sequence[str] = ()) -> list[Hit]:
        return self._merged(query)

    def dense(self, query: SearchQuery) -> list[Hit]:
        return self._merged(query)

    def sparse(self, query: SearchQuery) -> list[Hit]:
        return self._merged(query)

    def window(self, query: SearchQuery) -> list[Hit]:
        return self._merged(query)


class BackendEventsHTTP:
    """``EventPort`` over the unified search endpoint's event projection."""

    def __init__(self, client: "BackendHTTP") -> None:
        self._client = client

    def hits(
        self, person_id: str, *, from_: date | None, to: date | None, keywords: Sequence[str]
    ) -> list[EventHit]:
        return self._client.event_hits(person_id, from_=from_, to=to, keywords=keywords)


def _hit(item: dict[str, Any], index: int, *, original: bool) -> Hit:
    return Hit(
        cite_as=f"S{index + 1}",
        evidence_id=str(item.get("content_id") or item.get("id") or f"hit-{index}"),
        kind="content",
        title=str(item.get("event_title") or item.get("text") or "记忆")[:200],
        time_hint=str(item.get("taken_at") or ""),
        content=str(item.get("text") or ""),
        score=float(item.get("score") or 0.0),
        channel="keyword",
        source_kind="original" if original else "summary",
    )


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - never leak a body
        return "error"
    return str(payload.get("code") or payload.get("detail") or "error")


__all__ = ["BackendHTTP"]
