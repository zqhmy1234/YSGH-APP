"""Frozen contracts for the V3 agent harness (single source of truth).

Every module in ``agentv3`` depends on this file and on ``ports`` only. No module
outside ``adapters`` may import a database, HTTP or model library.

Field names and limits come from the V2 plan; the search protocol follows Linger
``apps/node-services/src/memory/README.md`` (@6663e21) where noted.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------------------------------------------------------------- limits

MAX_ALTERNATES = 6
MAX_ALTERNATE_CHARS = 200
FRAME_MAX_CHARS = 4200
EVIDENCE_MAX_CHARS = 1200
EVIDENCE_MAX_HITS = 8
DIGEST_MAX_CHARS = 600


class Strict(BaseModel):
    """All contracts forbid unknown fields: drift is a hard error, not a warning."""

    model_config = ConfigDict(extra="forbid", frozen=True)


# ---------------------------------------------------------------- enums


class Intent(str, Enum):
    CHITCHAT = "chitchat"
    RECALL = "recall"
    REMEMBER = "remember"
    EMOTION = "emotion"
    SETTING = "setting"
    OUT_OF_SCOPE = "out_of_scope"


class AgentName(str, Enum):
    MUSE = "muse"
    LIBRARIAN = "librarian"
    PROVENANCE = "provenance"
    SCULPTOR = "sculptor"
    SERENDIPITY = "serendipity"


class VerdictStatus(str, Enum):
    PASS = "pass"
    NEEDS_EVIDENCE = "needs_evidence"
    MASK = "mask"
    REJECT = "reject"
    HANDOFF = "handoff"


class WriteState(str, Enum):
    CONFIRMED = "confirmed"
    DRAFT = "draft"
    REJECTED = "rejected"


class MemoryLayer(str, Enum):
    NARRATIVE = "narrative"  # ① 陪伴叙事
    FACT = "fact"  # ② 用户事实
    PREFERENCE = "preference"  # ③ 偏好与修正
    ASSET = "asset"  # ④ 资产索引


class OpKind(str, Enum):
    ADD = "add"
    UPDATE = "update"
    RETRACT = "retract"
    CAME_FROM = "came_from"


class GuardAction(str, Enum):
    ALLOW = "allow"
    MASK = "mask"
    REJECT = "reject"
    HANDOFF = "handoff"


class ResultStatus(str, Enum):
    GROUNDED = "grounded"
    PARTIAL = "partial"
    EMPTY = "empty"


class TagSet(Strict):
    """Legacy V3 labels, kept separate from ``MemoryLayer`` lifecycle."""

    library: Literal["personal", "knowledge", ""] = ""
    category: str = ""
    content_type: str = ""
    mood: str = ""
    topics: tuple[str, ...] = ()
    content_format: Literal["text", "image", "voice", "link"] = "text"
    image_route: Literal["", "personal", "knowledge"] = ""


# ---------------------------------------------------------------- request


class TurnRequest(Strict):
    person_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    text: str = Field(min_length=1, max_length=2000)
    conversation_id: str = Field(default="default", min_length=1, max_length=128)
    locale: Literal["zh", "en"] = "zh"
    client_ts: datetime | None = None

    @field_validator("text")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("text must not be blank")
        return value


class TurnContext(Strict):
    """Everything ``plan()`` may look at. Pure data: no clock, no IO."""

    request: TurnRequest
    now: datetime
    degraded: tuple[str, ...] = ()  # names of ports currently degraded
    persona_version: str = "v1"
    hour: int = Field(ge=0, le=23, default=12)
    prefs: tuple[str, ...] = ()  # e.g. ("quiet_hours", "no_physical_contact")


class Budget(Strict):
    llm_calls: int = Field(ge=0, le=8, default=2)
    ms: int = Field(ge=100, le=30000, default=5500)
    chars: int = Field(ge=200, le=20000, default=4200)


class AgentInvocation(Strict):
    name: AgentName
    reason: str
    budget_ms: int = Field(ge=50, le=30000)
    budget_chars: int = Field(ge=100, le=20000)
    required: bool = True


class TurnPlan(Strict):
    intent: Intent
    invocations: tuple[AgentInvocation, ...]
    guards: tuple[str, ...]
    budget: Budget
    write_policy: Literal["none", "propose", "commit"]
    fallback: Literal["degraded_reply", "clarify", "refuse"]


# ---------------------------------------------------------------- search


class SearchQuery(Strict):
    """Canonical query produced ONLY by ``librarian.resolve_query``."""

    query: str = Field(min_length=1, max_length=400)
    person_id: str = Field(min_length=1, max_length=64)
    mode: Literal["evidence", "overview", "person", "time"] = "evidence"
    alternates: tuple[str, ...] = ()
    date_from: date | None = None
    date_to: date | None = None
    event_from: date | None = None
    event_to: date | None = None
    as_of: date | None = None
    limit: int = Field(ge=1, le=50, default=20)
    layers: tuple[MemoryLayer, ...] = ()

    @field_validator("alternates")
    @classmethod
    def _alternates(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if len(value) > MAX_ALTERNATES:
            raise ValueError(f"alternates must be <= {MAX_ALTERNATES}")
        for item in value:
            if len(item) > MAX_ALTERNATE_CHARS:
                raise ValueError(f"alternate too long (>{MAX_ALTERNATE_CHARS})")
            if not item.strip():
                raise ValueError("alternate must not be blank")
        return value

    @field_validator("date_to", "event_to")
    @classmethod
    def _range_order(cls, value: date | None, info):
        start = info.data.get("date_from") if info.field_name == "date_to" else info.data.get("event_from")
        if value is not None and start is not None and value < start:
            raise ValueError("range end must not precede start")
        return value


class TextChunk(Strict):
    """One retrieval chunk: offsets are into the original text (chunking parity)."""

    index: int = Field(ge=0)
    start: int = Field(ge=0)
    end: int = Field(ge=1)
    text: str = Field(min_length=1, max_length=1200)
    evidence_id: str = Field(min_length=1, max_length=100)


class Hit(Strict):
    cite_as: str = Field(min_length=1, max_length=8)  # S1..S20 / W1..W10
    evidence_id: str = Field(min_length=1, max_length=80)
    kind: Literal["room", "recording", "document", "content", "event", "wiki", "period"]
    title: str = Field(min_length=1, max_length=200)
    time_hint: str = ""
    content: str = ""
    score: float = 0.0
    channel: Literal["keyword", "dense", "sparse", "event", "time"] = "keyword"
    source_kind: Literal["original", "summary"] = "original"


class TraceItem(Strict):
    claim: str
    evidence_id: str
    channel: str


class SearchResult(Strict):
    hits: tuple[Hit, ...] = ()
    summaries: tuple[Hit, ...] = ()
    reason: str = ""
    relaxed: bool = False
    clarify: str | None = None
    event_hit_count: int = 0
    trace: tuple[TraceItem, ...] = ()
    degraded: bool = False
    gaps: tuple[str, ...] = ()
    status: ResultStatus = ResultStatus.EMPTY

    @property
    def evidence_ids(self) -> tuple[str, ...]:
        return tuple(h.evidence_id for h in (*self.hits, *self.summaries))


# ---------------------------------------------------------------- provenance


class Claim(Strict):
    text: str
    cites: tuple[str, ...] = ()
    kind: Literal["plain", "private", "numeric", "date", "name"] = "plain"


class ClaimVerdict(Strict):
    text: str
    grounded: bool
    evidence_ids: tuple[str, ...] = ()
    reason: str = ""


class ClaimBundle(Strict):
    """A reply split into claims plus the evidence the turn actually holds."""

    claims: tuple[Claim, ...] = ()
    evidence: tuple[str, ...] = ()
    persona_rules: tuple[str, ...] = ()
    crisis: bool = False


class EventHit(Strict):
    event_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=200)
    day: str = ""
    summary: str = ""
    score: float = 0.0


class Verdict(Strict):
    status: VerdictStatus
    claims: tuple[ClaimVerdict, ...] = ()
    reason: str = ""
    fixed_reply: str | None = None


# ---------------------------------------------------------------- memory


class MemoryProposal(Strict):
    op: OpKind
    layer: MemoryLayer
    text: str = Field(min_length=1, max_length=400)
    entry_id: str | None = None
    evidence: tuple[str, ...] = ()
    valid_from: date | None = None
    recorded_at: datetime | None = None
    reason: str = ""
    tags: TagSet = Field(default_factory=TagSet)


class WriteReceipt(Strict):
    state: WriteState
    entry_id: str | None = None
    reason: str = ""
    degraded: bool = False


class ProposalBatch(Strict):
    person_id: str
    batch_hash: str = Field(min_length=8, max_length=64)
    proposals: tuple[MemoryProposal, ...] = ()


class CommitReceipt(Strict):
    batch_hash: str
    committed: tuple[str, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()  # (entry_or_index, issue_code)
    idempotent: bool = False
    rolled_back: bool = False


class MemoryEntry(Strict):
    entry_id: str
    person_id: str
    layer: MemoryLayer
    text: str
    valid_from: date
    valid_to: date | None = None
    recorded_at: datetime
    superseded_by: str | None = None
    evidence: tuple[str, ...] = ()
    correction: bool = False
    tags: TagSet = Field(default_factory=TagSet)


class PurgeScope(Strict):
    person_id: str
    layers: tuple[MemoryLayer, ...] = ()
    entry_ids: tuple[str, ...] = ()


class PurgeReceipt(Strict):
    purged: int
    layers: tuple[MemoryLayer, ...] = ()


class DigestSection(Strict):
    name: str
    text: str


class Digest(Strict):
    person_id: str
    sections: tuple[DigestSection, ...] = ()
    state: Literal["ok", "insufficient_data", "unavailable"] = "ok"

    @property
    def chars(self) -> int:
        return sum(len(s.text) for s in self.sections)


# ---------------------------------------------------------------- guard


class GuardRequest(Strict):
    person_id: str
    text: str
    intent: Intent


class GuardVerdict(Strict):
    action: GuardAction
    reason: str = ""
    reply: str | None = None


# ---------------------------------------------------------------- domains (six-domain slice)

class SourceKind(str, Enum):
    TEXT = "text"
    PHOTO = "photo"
    VOICE = "voice"
    WECHAT = "wechat"
    IMPORT = "import"


class Sensitivity(str, Enum):
    PUBLIC = "public"
    PERSONAL = "personal"
    SENSITIVE = "sensitive"
    CRITICAL = "critical"


class FamilyRole(str, Enum):
    OWNER = "owner"
    ADULT = "adult"
    TEEN = "teen"
    CHILD = "child"
    GUEST = "guest"


class FamilyAction(str, Enum):
    READ = "read"
    WRITE = "write"
    EXPORT = "export"
    DELETE = "delete"
    SHARE = "share"


class Episode(Strict):
    episode_id: str = Field(min_length=1, max_length=64)
    person_id: str = Field(min_length=1, max_length=64)
    source: SourceKind
    text: str = Field(min_length=1, max_length=2000)
    day: date
    place: str = Field(default="", max_length=64)
    people: tuple[str, ...] = ()
    evidence_id: str = Field(min_length=1, max_length=100)
    sensitivity: Sensitivity = Sensitivity.PERSONAL
    tags: TagSet = Field(default_factory=TagSet)


class IngestReceipt(Strict):
    accepted: tuple[str, ...] = ()
    rejected: tuple[tuple[str, int], ...] = ()  # (reason, count)


class EventNode(Strict):
    node_id: str
    layer: Literal["moment", "activity", "episode", "chapter"]
    title: str
    day: date
    member_ids: tuple[str, ...] = ()


class PermissionDecision(Strict):
    allow: bool
    reason: str = ""


class EchoCandidate(Strict):
    echo_id: str
    person_id: str
    title: str
    reason: Literal["anniversary", "season", "place", "person", "topic"]
    sensitivity: Sensitivity
    score: float = Field(ge=0.0, le=1.0)
    why_now: str


# ---------------------------------------------------------------- turn result


class ToolCall(Strict):
    name: str = Field(min_length=1, max_length=64)
    arguments: dict[str, str] = Field(default_factory=dict)


class ToolResult(Strict):
    name: str
    ok: bool
    content: str = ""
    reason: str = ""


class LLMRequest(Strict):
    """One model call. ``purpose`` is for tracing/cost, never for routing."""

    purpose: Literal["muse", "librarian_rewrite", "provenance", "sculptor", "digest"] = "muse"
    system: str = Field(min_length=1, max_length=8000)
    user: str = Field(min_length=1, max_length=8000)
    tools: tuple[str, ...] = ()
    tool_schemas: tuple[dict[str, Any], ...] = ()
    max_chars: int = Field(ge=50, le=4000, default=1200)
    temperature: float = Field(ge=0.0, le=2.0, default=0.3)


class LLMResponse(Strict):
    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    finish: Literal["stop", "tool_calls", "length"] = "stop"


class Cost(Strict):
    llm_calls: int = 0
    ms: int = 0
    chars: int = 0


class FrameSegment(Strict):
    name: Literal[
        "persona",
        "relation",
        "boundaries",
        "corrections",
        "digest",
        "narrative",
        "evidence",
        "task",
        "crisis",
    ]
    text: str
    stable: bool = False  # stable segments are cache-keyed by persona version


class Frame(Strict):
    segments: tuple[FrameSegment, ...]
    dropped: tuple[str, ...] = ()
    total_chars: int = 0

    def render(self) -> str:
        return "\n\n".join(f"## {s.name}\n{s.text}" for s in self.segments if s.text)


class TurnResult(Strict):
    reply: str
    cites: tuple[str, ...] = ()
    verdict: Verdict | None = None
    receipts: tuple[WriteReceipt, ...] = ()
    degraded: bool = False
    degraded_ports: tuple[str, ...] = ()
    status: ResultStatus = ResultStatus.GROUNDED
    trace: tuple[TraceItem, ...] = ()
    cost: Cost = Cost()
    plan: TurnPlan | None = None
