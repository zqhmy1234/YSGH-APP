"""Agent HTTP boundary: thin FastAPI routes, no business logic.

Surfaces (plan §11.1):
  * ``/v1/chat``         — legacy-compatible chat (non-stream + SSE)
  * ``/v1/health``       — liveness + dependency states
  * ``/v1/cards``        — list/activate internal RoleCards
  * ``/v1/v3/*``         — v3 compatibility (turn/digest/memory/purge/ingest/echo/family)
  * ``/v1/v3/models*``   — Lingqu model catalog (admin, token protected)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from adapters.lingqu.admin import list_models
from adapters.lingqu.contracts import Health
from harness.contracts import (
    Digest,
    EchoCandidate,
    FamilyRole,
    IngestReceipt,
    MemoryEntry,
    PurgeReceipt,
    PurgeScope,
    TurnRequest,
    TurnResult,
)
from harness.domain.echo import EchoPolicy
from harness.domain.family import FamilyPolicy
from harness.domain.ingest import Ingestor
from harness.orchestrator import Orchestrator
from memory.ledger import MemoryStore
from memory.projection import build as build_digest
from prompts.bundle import build_bundle
from prompts.cards import CardError, CardSafetyPolicy, parse_card, sanitize_card
from prompts.role_card import DEFAULT_ROLE_ID, RoleCardRepository


class IngestRequest(BaseModel):
    person_id: str = Field(min_length=1, max_length=64)
    items: list[dict] = Field(default_factory=list)


class ModelSwitchRequest(BaseModel):
    profile_id: str = Field(min_length=3, max_length=100)


class ModelProbeRequest(BaseModel):
    profile_id: str | None = Field(default=None, max_length=100)


class CardActivateRequest(BaseModel):
    role_id: str = Field(default=DEFAULT_ROLE_ID, min_length=1, max_length=100)


class ChatAnyRequest(BaseModel):
    """``/v1/chat`` accepts two live shapes, because two clients exist.

    * **backend** (``services/external/agent.py``) sends
      ``{user_id, message, thread_id}``;
    * the console / older tooling sends ``{person_id, text, conversation_id, stream}``.

    Rejecting either one breaks a live caller, so both are normalized here
    instead of forcing every caller to migrate at once.
    """

    person_id: str | None = Field(default=None, max_length=64)
    text: str | None = Field(default=None, max_length=2000)
    conversation_id: str = "default"
    stream: bool = False
    user_id: str | None = Field(default=None, max_length=64)
    message: str | None = Field(default=None, max_length=2000)
    thread_id: str | None = Field(default=None, max_length=160)
    persona: dict | None = None
    response_brief: dict | None = None

    @property
    def is_backend_shape(self) -> bool:
        return bool(self.user_id and self.message)

    def normalized(self) -> tuple[str, str, str]:
        if self.is_backend_shape:
            person, conversation = _split_thread_id(self.thread_id or "", self.user_id or "")
            return person, self.message or "", conversation
        if not (self.person_id and self.text):
            raise ValueError("either person_id+text or user_id+message is required")
        return self.person_id, self.text, self.conversation_id


class BackendChatRequest(BaseModel):
    """The wire shape the backend already sends (see services/external/agent.py).

    ``user_id`` is injected by the backend from the JWT; ``message`` is the user
    text; ``thread_id`` is ``f"{user_id}:{conversation_id}"``. Accepting this
    shape on the agent side is what makes backend→agent chat actually work —
    the V3 rewrite must not silently change a live cross-service contract.
    """

    user_id: str = Field(min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=2000)
    thread_id: str = Field(default="default", min_length=1, max_length=160)
    persona: dict | None = None
    response_brief: dict | None = None


_BRIEF_LABELS = {
    "what_she_sees": "【这一轮她看见什么】",
    "what_they_need": "【这一轮对方更需要什么】",
    "how_she_begins": "【她第一句怎么做】",
    "what_she_avoids": "【她这一轮尤其不能做什么】",
    "how_much_she_says": "【她这一轮说多长】",
    "what_she_heard": "【她听见的原话】",
    "how_sure_she_is": "【她有多确定】",
}


def _brief_prompt(payload: dict | None) -> str:
    """Turn the wire brief into full prose; never pass its internal id."""
    if not payload:
        return ""
    return "\n".join(
        f"{label}{payload[key]}"
        for key, label in _BRIEF_LABELS.items()
        if str(payload.get(key) or "").strip()
    )


@dataclass
class Container:
    orchestrator: Orchestrator
    store: MemoryStore
    token: str = ""
    episodes: list = field(default_factory=list)
    gateway: object | None = None
    selection: object | None = None
    tools: object | None = None
    cards: object | None = None
    active_card: object | None = None
    card_repository: RoleCardRepository | None = None
    backend: object | None = None


def _split_thread_id(thread_id: str, user_id: str) -> tuple[str, str]:
    """``f"{user_id}:{conversation_id}"`` -> ``(person_id, conversation_id)``.

    ``person_id`` is restricted to ``[A-Za-z0-9_-]`` by TurnRequest, so the
    colon-separated backend thread id cannot be used verbatim. Splitting on the
    first colon keeps per-conversation history intact.
    """
    person, _, conversation = thread_id.partition(":")
    return (person or user_id, conversation or thread_id)


def create_app(container: Container) -> FastAPI:
    app = FastAPI(title="yishu-agent", version="4.0")
    repository = container.card_repository or RoleCardRepository()
    # B7-05: the card is selected per request from the backend's persona
    # snapshot. Keep a default only for the read-only /v1/cards listing; never
    # mutate a process-wide prompt from here.
    if container.active_card is None:
        try:
            container.active_card = repository.load(DEFAULT_ROLE_ID)
        except Exception:  # noqa: BLE001 —— 坏卡不阻断启动；单轮仍走安全默认人设
            pass

    def _auth(x_agent_token: str | None = Header(default=None)) -> None:
        if container.token and x_agent_token != container.token:
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/health")
    @app.get("/v1/health")
    def health() -> dict[str, object]:
        deps: dict[str, str] = {"agent": "ok"}
        if container.gateway is None:
            deps["llm"] = "unconfigured"
        else:
            try:
                gateway = container.gateway
                doctor = gateway.doctor()
                active = gateway.active().id
                row = next((item for item in doctor if item["profile"] == active), {})
                deps["llm"] = "ok" if row.get("credential") != "missing" else "missing-key"
            except Exception:  # noqa: BLE001 - health must never crash
                deps["llm"] = "degraded"
        return {"status": "ok", "version": "4.0", "deps": deps}

    def _run_turn(
        person_id: str,
        text: str,
        conversation_id: str,
        *,
        persona: dict | None = None,
        response_brief: dict | None = None,
    ) -> TurnResult:
        role_id = str((persona or {}).get("role_id") or DEFAULT_ROLE_ID)
        try:
            card = repository.load(role_id)
            persona_text = card.render()
        except Exception:  # noqa: BLE001 - a bad card must not kill the turn
            card = repository.load(DEFAULT_ROLE_ID)
            persona_text = card.render()
        request = TurnRequest(
            person_id=person_id,
            text=text,
            conversation_id=conversation_id,
            persona=persona_text,
            relationship_note=str((persona or {}).get("relationship_note") or ""),
            response_brief=_brief_prompt(response_brief),
        )
        return container.orchestrator.run(request)

    def _chat_body(result: TurnResult) -> dict:
        return {
            "reply": result.reply,
            "cites": list(result.cites),
            "status": result.status.value,
            "degraded": result.degraded,
            "degraded_ports": list(result.degraded_ports),
            "cost": result.cost.model_dump(),
            "plan": result.plan.model_dump(mode="json") if result.plan else None,
        }

    def _stream_frames(result: TurnResult) -> Iterator[bytes]:
        body = _chat_body(result)
        yield _sse("start", {"status": "ok"})
        yield _sse("delta", {"text": body["reply"]})
        # The backend's streaming endpoint reads the final text from here.
        yield _sse("done", {**body, "latency_ms": int(result.cost.ms)})

    @app.post("/v1/chat", dependencies=[Depends(_auth)])
    def chat(payload: ChatAnyRequest):
        try:
            person_id, text, conversation_id = payload.normalized()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        result = _run_turn(
            person_id,
            text,
            conversation_id,
            persona=payload.persona,
            response_brief=payload.response_brief,
        )
        if not payload.stream:
            return _chat_body(result)
        return StreamingResponse(_stream_frames(result), media_type="text/event-stream")

    def _turn_from_backend(payload: BackendChatRequest) -> TurnResult:
        """Map the backend's wire shape onto ``TurnRequest`` without losing the thread."""
        person_id, conversation = _split_thread_id(payload.thread_id, payload.user_id)
        return _run_turn(
            person_id,
            payload.message,
            conversation,
            persona=payload.persona,
            response_brief=payload.response_brief,
        )

    @app.post("/v1/chat/stream", dependencies=[Depends(_auth)])
    def chat_stream(payload: BackendChatRequest):
        """Backend-facing SSE: same shape the live backend already consumes."""
        return StreamingResponse(_stream_frames(_turn_from_backend(payload)), media_type="text/event-stream")

    @app.post("/v1/backend/chat", dependencies=[Depends(_auth)])
    def backend_chat(payload: BackendChatRequest):
        """Backend-facing non-stream chat (same wire shape as ``chat_stream``)."""
        return _chat_body(_turn_from_backend(payload))

    @app.post("/v1/v3/turn", dependencies=[Depends(_auth)])
    def turn(payload: TurnRequest) -> TurnResult:
        return container.orchestrator.run(payload)

    @app.get("/v1/v3/digest/{person_id}", dependencies=[Depends(_auth)])
    def digest(person_id: str) -> Digest:
        return build_digest(container.store.entries(person_id), person_id)

    @app.get("/v1/v3/memory/{person_id}", dependencies=[Depends(_auth)])
    def memory(person_id: str) -> list[MemoryEntry]:
        return container.store.entries(person_id)

    @app.post("/v1/v3/purge", dependencies=[Depends(_auth)])
    def purge(scope: PurgeScope) -> PurgeReceipt:
        return container.store.purge(scope)

    @app.post("/v1/v3/ingest", dependencies=[Depends(_auth)])
    def ingest(payload: IngestRequest) -> IngestReceipt:
        ingestor = Ingestor(today=datetime.now().astimezone().date())
        episodes, receipt = ingestor.ingest(payload.person_id, payload.items)
        container.episodes.extend(episodes)
        return receipt

    @app.get("/v1/v3/echo/{person_id}", dependencies=[Depends(_auth)])
    def echo(person_id: str) -> list[EchoCandidate]:
        if not container.episodes:
            return []
        return EchoPolicy().candidates(person_id, list(container.episodes), datetime.now().astimezone().date())

    @app.get("/v1/v3/family/{role}", dependencies=[Depends(_auth)])
    def family(role: FamilyRole) -> dict:
        return FamilyPolicy().describe(role)

    # ---------------------------------------------------------------- cards
    @app.get("/v1/cards", dependencies=[Depends(_auth)])
    @app.get("/v1/v3/cards", dependencies=[Depends(_auth)])
    def cards() -> dict:
        items = []
        for role_id in repository.available():
            try:
                card = repository.load(role_id)
            except Exception:  # noqa: BLE001 - a broken asset must not hide the rest
                continue
            items.append(
                {
                    "role_id": card.role_id,
                    "display_name": card.display_name,
                    "archetype": card.archetype,
                    "version": card.version,
                    "card_hash": card.card_hash,
                    "active": getattr(container.active_card, "role_id", "") == card.role_id,
                }
            )
        return {"cards": items, "active": getattr(container.active_card, "role_id", "")}

    @app.post("/v1/cards/active", dependencies=[Depends(_auth)])
    @app.post("/v1/v3/cards/active", dependencies=[Depends(_auth)])
    def activate_card(request: CardActivateRequest) -> dict:
        card = repository.load(request.role_id)
        return {
            "active_card": card.role_id,
            "card_hash": card.card_hash,
            "version": card.version,
            "preview": True,
            "deprecated": True,
            "note": "用户级角色卡请在后端 PUT /api/v1/persona 选择；该接口不再改全局 prompt",
        }

    @app.post("/v1/v3/cards/import", dependencies=[Depends(_auth)])
    def import_card(payload: dict) -> dict:
        try:
            card = sanitize_card(parse_card(payload))
        except CardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        issues = CardSafetyPolicy().check(card)
        return {"name": card.name, "issues": issues, "system_prompt_ignored": bool(card.system_prompt)}

    # ---------------------------------------------------------------- models
    @app.get("/v1/v3/models", dependencies=[Depends(_auth)])
    def models() -> dict:
        if container.gateway is None:
            raise HTTPException(status_code=503, detail="lingqu gateway not configured")
        return list_models(container.gateway)

    @app.get("/v1/v3/models/active", dependencies=[Depends(_auth)])
    def active_model() -> dict:
        if container.gateway is None:
            raise HTTPException(status_code=503, detail="lingqu gateway not configured")
        data = list_models(container.gateway)
        active = next((item for item in data["profiles"] if item["is_active"]), None)
        if active is None:
            raise HTTPException(status_code=503, detail="active profile missing")
        return active

    @app.put("/v1/v3/models/active", dependencies=[Depends(_auth)])
    def switch_model(request: ModelSwitchRequest) -> dict:
        if container.gateway is None:
            raise HTTPException(status_code=503, detail="lingqu gateway not configured")
        try:
            container.gateway.set_active(request.profile_id)
        except Exception as exc:  # noqa: BLE001 - unknown id is a 404 contract
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        if container.selection is not None:
            container.selection.save(request.profile_id)
        return {"active": request.profile_id}

    @app.post("/v1/v3/models/probe", dependencies=[Depends(_auth)])
    def probe_model(request: ModelProbeRequest | None = None) -> Health:
        if container.gateway is None:
            raise HTTPException(status_code=503, detail="lingqu gateway not configured")
        return container.gateway.probe(request.profile_id if request else None)

    return app


def _sse(event: str, payload: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n".encode()


def app_from_env() -> FastAPI:  # pragma: no cover - wiring only
    from adapters.backend_http import BackendEventsHTTP, BackendHTTP, BackendSearchHTTP
    from adapters.lingqu.admin import SelectionStore
    from adapters.lingqu.gateway import LingquGateway
    from adapters.sqlite_memory import SqliteMemoryStore
    from tools.runtime import READ_TOOLS, MemoryToolService

    gateway = LingquGateway()
    selection = SelectionStore()
    selected = selection.load(gateway.catalog.default_profile)
    if gateway.catalog.has(selected):
        gateway.set_active(selected)
    # One HTTP client for all six L3 endpoints; the base URL is the backend.
    backend_url = os.environ.get("BACKEND_BASE_URL") or os.environ.get("SEARCH_BASE_URL", "")
    backend_token = os.environ.get("INTERNAL_TOKEN") or os.environ.get("AGENT_SERVICE_TOKEN", "")
    backend_http = BackendHTTP(backend_url, backend_token, timeout_s=8.0)

    # B5 合并：配了 backend 就走 canonical 账本（后端写入端点 + B6 授权闸），
    # 不再自写本地 JSON/SQLite。无 backend 的本地调试/角色扮演仍用 Sqlite 降级。
    if backend_url:
        from adapters.backend_memory import BackendMemoryStore

        store = BackendMemoryStore(backend_http)
    else:
        store = SqliteMemoryStore(os.environ.get("AGENTV3_MEMORY_PATH", ":memory:"))
    search_port = BackendSearchHTTP(backend_http)
    events_port = BackendEventsHTTP(backend_http)
    tool_service = MemoryToolService.default(store)
    read_handlers = {name: tool_service.handlers()[name] for name in READ_TOOLS}
    read_schemas = tuple(s for s in tool_service.schemas() if s["function"]["name"] in READ_TOOLS)
    orchestrator = Orchestrator(
        llm=gateway,
        search=search_port,
        events=events_port,
        store=store,
        tools=read_handlers,
        tool_schemas=read_schemas,
        system_prefix=build_bundle().render(),
    )
    orchestrator.set_backend(backend_http)
    return create_app(
        Container(
            orchestrator=orchestrator,
            store=store,
            token=os.environ.get("AGENT_V3_TOKEN", ""),
            gateway=gateway,
            selection=selection,
            tools=tool_service,
            backend=backend_http,
        )
    )
