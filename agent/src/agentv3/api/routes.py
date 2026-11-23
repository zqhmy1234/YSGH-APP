"""HTTP boundary: five v3 endpoints, one legacy-compatible route, no business logic."""

from __future__ import annotations

import os
from dataclasses import dataclass

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel, Field

from ..contracts import (
    Digest,
    EchoCandidate,
    Episode,
    FamilyRole,
    IngestReceipt,
    MemoryEntry,
    PurgeReceipt,
    PurgeScope,
    TurnRequest,
    TurnResult,
)
from ..domain.echo import EchoPolicy
from ..domain.family import FamilyPolicy
from ..domain.ingest import Ingestor
from ..lingqu.admin import list_models
from ..lingqu.contracts import Health
from ..prompts.bundle import build_bundle
from ..prompts.cards import CardError, CardSafetyPolicy, parse_card, sanitize_card
from ..memory.ledger import MemoryStore
from ..memory.projection import build as build_digest
from ..orchestrator import Orchestrator


class IngestRequest(BaseModel):
    person_id: str = Field(min_length=1, max_length=64)
    items: list[dict] = Field(default_factory=list)


class ModelSwitchRequest(BaseModel):
    profile_id: str = Field(min_length=3, max_length=100)


class ModelProbeRequest(BaseModel):
    profile_id: str | None = Field(default=None, max_length=100)


class CardActivateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)


@dataclass
class Container:
    orchestrator: Orchestrator
    store: MemoryStore
    token: str = ""
    episodes: list | None = None
    gateway: object | None = None
    selection: object | None = None
    tools: object | None = None
    cards: list | None = None
    active_card: object | None = None


def create_app(container: Container) -> FastAPI:
    app = FastAPI(title="yishu-agent-v3", version="3.0")

    def _auth(x_agent_token: str | None = Header(default=None)) -> None:
        if container.token and x_agent_token != container.token:
            raise HTTPException(status_code=401, detail="bad token")

    @app.get("/health")
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
        return {"status": "ok", "version": "3.0", "deps": deps}

    @app.get("/v1/v3/cards", dependencies=[Depends(_auth)])
    def cards() -> dict:
        items = container.cards or []
        return {"cards": [{"name": c.name, "creator": c.creator, "tags": list(c.tags)} for c in items]}

    @app.post("/v1/v3/cards/import", dependencies=[Depends(_auth)])
    def import_card(payload: dict) -> dict:
        try:
            card = sanitize_card(parse_card(payload))
        except CardError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        issues = CardSafetyPolicy().check(card)
        if container.cards is None:
            container.cards = []
        cards = container.cards
        cards[:] = [c for c in cards if c.name != card.name]
        cards.append(card)
        return {"name": card.name, "issues": issues, "system_prompt_ignored": bool(card.system_prompt)}

    @app.post("/v1/v3/cards/active", dependencies=[Depends(_auth)])
    def activate_card(request: CardActivateRequest) -> dict:
        items = container.cards or []
        card = next((c for c in items if c.name == request.name), None)
        if card is None:
            raise HTTPException(status_code=404, detail="card not found")
        container.orchestrator.set_system_prefix(build_bundle(card=card).render())
        container.active_card = card
        return {"active_card": card.name, "segments": 10}

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
            profile = container.gateway.set_active(request.profile_id)
        except Exception as exc:  # noqa: BLE001 - unknown profile is a 404
            raise HTTPException(status_code=404, detail=f"unknown profile: {request.profile_id}") from exc
        if container.selection is not None:
            container.selection.save(profile.id)
        return {"active_profile": profile.id}

    @app.post("/v1/v3/models/probe", response_model=Health, dependencies=[Depends(_auth)])
    def probe_model(request: ModelProbeRequest) -> Health:
        if container.gateway is None:
            raise HTTPException(status_code=503, detail="lingqu gateway not configured")
        try:
            return container.gateway.probe(request.profile_id)
        except Exception as exc:  # noqa: BLE001 - unknown profile is a 404
            raise HTTPException(status_code=404, detail=f"unknown profile: {request.profile_id}") from exc

    @app.post("/v1/v3/turn", response_model=TurnResult, dependencies=[Depends(_auth)])
    def turn(request: TurnRequest) -> TurnResult:
        return container.orchestrator.run(request)

    @app.get("/v1/v3/digest/{person_id}", response_model=Digest, dependencies=[Depends(_auth)])
    def digest(person_id: str) -> Digest:
        return build_digest(container.store.entries(person_id), person_id)

    @app.get("/v1/v3/memory/{person_id}", response_model=list[MemoryEntry], dependencies=[Depends(_auth)])
    def memory(person_id: str) -> list[MemoryEntry]:
        return container.store.entries(person_id)

    @app.post("/v1/v3/purge", response_model=PurgeReceipt, dependencies=[Depends(_auth)])
    def purge(scope: PurgeScope) -> PurgeReceipt:
        return container.store.purge(scope)

    @app.post("/v1/v3/ingest", response_model=IngestReceipt, dependencies=[Depends(_auth)])
    def ingest(request: IngestRequest) -> IngestReceipt:
        episodes, receipt = Ingestor().ingest(request.person_id, request.items)
        if container.episodes is None:
            container.episodes = []
        container.episodes.extend(episodes)
        return receipt

    @app.get("/v1/v3/echo/{person_id}", response_model=list[EchoCandidate], dependencies=[Depends(_auth)])
    def echo(person_id: str) -> list[EchoCandidate]:
        from datetime import date

        return EchoPolicy().candidates(person_id, container.episodes or [], date.today())

    @app.get("/v1/v3/family/{role}", dependencies=[Depends(_auth)])
    def family(role: FamilyRole) -> dict[str, list[str]]:
        return FamilyPolicy().describe(role)

    @app.post("/v1/chat", response_model=TurnResult, dependencies=[Depends(_auth)])
    def legacy_chat(request: TurnRequest) -> TurnResult:
        """Compatibility shim: old callers keep working, semantics are v3."""
        return container.orchestrator.run(request)

    return app


def app_from_env() -> FastAPI:  # pragma: no cover - wiring only
    from ..adapters.backend import BackendEvents, BackendSearch
    from ..adapters.sqlite_memory import SqliteMemoryStore
    from ..lingqu.admin import SelectionStore
    from ..lingqu.gateway import LingquGateway
    from ..prompts.bundle import build_bundle
    from ..services.tools import READ_TOOLS, MemoryToolService

    store = SqliteMemoryStore(os.environ.get("AGENTV3_MEMORY_PATH", ":memory:"))
    gateway = LingquGateway()
    selection = SelectionStore()
    selected = selection.load(gateway.catalog.default_profile)
    if gateway.catalog.has(selected):
        gateway.set_active(selected)
    search_url = os.environ.get("SEARCH_BASE_URL", "")
    events_url = os.environ.get("EVENT_BASE_URL", "")
    tool_service = MemoryToolService.default(store)
    read_handlers = {name: tool_service.handlers()[name] for name in READ_TOOLS}
    read_schemas = tuple(s for s in tool_service.schemas() if s["function"]["name"] in READ_TOOLS)
    orchestrator = Orchestrator(
        llm=gateway,
        search=BackendSearch(search_url, os.environ.get("INTERNAL_TOKEN", "")),
        events=BackendEvents(events_url, os.environ.get("INTERNAL_TOKEN", "")),
        store=store,
        tools=read_handlers,
        tool_schemas=read_schemas,
        system_prefix=build_bundle().render(),
    )
    return create_app(
        Container(
            orchestrator=orchestrator,
            store=store,
            token=os.environ.get("AGENT_V3_TOKEN", ""),
            gateway=gateway,
            selection=selection,
            tools=tool_service,
        )
    )
