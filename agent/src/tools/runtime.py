"""The 13 legacy tools, expressed as services over V3 ports.

The old ``tools/memory_tools.py`` cannot be imported here: it depended on
LangChain's decorator, a Supabase client and the ``yishu`` request context. That
was a transport layer, not the capability. Each capability is re-expressed below
against the ledger/Sculptor/Ingestor; adapters (HTTP fetch, URL parsing) stay in
the composition root and are injected as callables.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from agents.sculptor import Sculptor
from harness.contracts import (
    MemoryEntry,
    MemoryLayer,
    MemoryProposal,
    OpKind,
    ProposalBatch,
    PurgeScope,
    ToolResult,
)
from harness.domain.ingest import Ingestor
from memory.ledger import MemoryStore
from memory.projection import build as build_digest

ToolFn = Callable[[dict[str, str]], ToolResult]

# The 8 action tools and 5 read/stat tools from the legacy SOP.
ACTION_TOOLS = (
    "save_memory",
    "update_memory_summary",
    "delete_memory",
    "save_knowledge_collection",
    "delete_knowledge_collection",
    "export_knowledge_collections",
    "link_memory_to_collection",
    "fetch_url_content",
)
READ_TOOLS = (
    "search_memories",
    "search_knowledge_collections",
    "get_recent_memories",
    "get_memory_stats",
    "get_knowledge_summary",
)
TOOL_NAMES = ACTION_TOOLS + READ_TOOLS


@dataclass
class MemoryToolService:
    store: MemoryStore
    sculptor: Sculptor
    ingestor: Ingestor = field(default_factory=Ingestor)
    fetch: Callable[[str], str] | None = None
    summaries: dict[str, str] = field(default_factory=dict)

    @classmethod
    def default(cls, store: MemoryStore) -> MemoryToolService:
        """Composition helper; keeps the API layer from importing an Agent."""
        return cls(store=store, sculptor=Sculptor(store))

    # -- registry -----------------------------------------------------
    def handlers(self) -> dict[str, ToolFn]:
        return {name: getattr(self, name) for name in TOOL_NAMES}

    @staticmethod
    def schemas() -> tuple[dict[str, Any], ...]:
        """OpenAI-compatible tool schemas; descriptions come from the old SOP."""
        defs = {
            "save_memory": (
                "保存一条个人记忆（叙事/事实/偏好等）。",
                {
                    "person_id": "string",
                    "text": "string",
                    "source": "string",
                    "people": "array",
                    "place": "string",
                    "day": "string",
                },
                ("person_id", "text"),
            ),
            "update_memory_summary": (
                "刷新某个人的摘要缓存，不写记忆账本。",
                {"person_id": "string", "text": "string"},
                ("person_id", "text"),
            ),
            "delete_memory": (
                "删除某条个人记忆，必须给出 person_id 与 entry_id。",
                {"person_id": "string", "entry_id": "string"},
                ("person_id", "entry_id"),
            ),
            "save_knowledge_collection": (
                "保存外部知识收藏（链接/文章/图片），进入资产层。",
                {
                    "person_id": "string",
                    "title": "string",
                    "url": "string",
                    "summary": "string",
                    "content_id": "string",
                },
                ("person_id", "title"),
            ),
            "delete_knowledge_collection": (
                "删除知识收藏；给 content_id 精确删，否则按资产层清理。",
                {"person_id": "string", "content_id": "string"},
                ("person_id",),
            ),
            "export_knowledge_collections": (
                "导出知识收藏为 JSON 或 Markdown。",
                {"person_id": "string", "format": "string"},
                ("person_id",),
            ),
            "link_memory_to_collection": (
                "把个人记忆挂到知识收藏上。",
                {"person_id": "string", "memory_id": "string", "collection_id": "string", "note": "string"},
                ("person_id", "memory_id", "collection_id"),
            ),
            "fetch_url_content": ("抓取网页正文；只允许 http/https，拒绝内网地址。", {"url": "string"}, ("url",)),
            "search_memories": (
                "按关键词检索个人记忆。",
                {"person_id": "string", "query": "string", "limit": "integer"},
                ("person_id", "query"),
            ),
            "search_knowledge_collections": (
                "按关键词检索知识收藏。",
                {"person_id": "string", "query": "string", "limit": "integer"},
                ("person_id", "query"),
            ),
            "get_recent_memories": ("读取最近记忆。", {"person_id": "string", "limit": "integer"}, ("person_id",)),
            "get_memory_stats": ("统计记忆层与标签数量。", {"person_id": "string"}, ("person_id",)),
            "get_knowledge_summary": (
                "生成知识收藏摘要。",
                {"person_id": "string", "budget_chars": "integer"},
                ("person_id",),
            ),
        }
        out = []
        for name, (desc, params, required) in defs.items():
            props: dict[str, Any] = {}
            for key, typ in params.items():
                props[key] = (
                    {"type": "integer"}
                    if typ == "integer"
                    else {"type": "array", "items": {"type": "string"}}
                    if typ == "array"
                    else {"type": "string"}
                )
            out.append(
                {
                    "type": "function",
                    "function": {
                        "name": name,
                        "description": desc,
                        "parameters": {"type": "object", "properties": props, "required": list(required)},
                    },
                }
            )
        return tuple(out)

    # -- 8 actions ----------------------------------------------------
    def save_memory(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        text = _text(args, "text")
        source = args.get("source") or "text"
        people = _list(args.get("people"))
        raw = {
            "text": text,
            "source": source,
            "people": people,
            "place": args.get("place", ""),
            "day": args.get("day", ""),
            "origin": "personal",
        }
        episodes, receipt = self.ingestor.ingest(person, [raw])
        if not episodes:
            return _err("save_memory", f"rejected:{receipt.rejected}")
        ep = episodes[0].model_dump()
        ep["layer"] = MemoryLayer.NARRATIVE.value
        batch = _batch(
            person,
            (
                MemoryProposal(
                    op=OpKind.ADD,
                    layer=MemoryLayer.NARRATIVE,
                    text=text,
                    evidence=(episodes[0].evidence_id,),
                    reason="tool_save",
                    tags=episodes[0].tags,
                ),
            ),
        )
        committed = self.sculptor.apply(batch, allowed_evidence={episodes[0].evidence_id})
        if committed.rejected:
            return _err("save_memory", f"rejected:{committed.rejected}")
        return _ok(
            "save_memory", {"entry_id": committed.committed[0] if committed.committed else "", "tags": ep["tags"]}
        )

    def update_memory_summary(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        text = _text(args, "text")
        if len(text) > 2000:
            return _err("update_memory_summary", "summary_too_long")
        self.summaries[person] = text
        return _ok("update_memory_summary", {"person_id": person, "chars": len(text)})

    def delete_memory(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        entry_id = _text(args, "entry_id")
        receipt = self.store.purge(PurgeScope(person_id=person, entry_ids=(entry_id,)))
        return _ok("delete_memory", receipt.model_dump())

    def save_knowledge_collection(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        title = _text(args, "title")
        url = args.get("url", "").strip()
        summary = args.get("summary", "").strip()
        content_id = args.get("content_id", "").strip() or _stable_id("asset", person, url or title)
        text = "\n".join(part for part in (title, url, summary) if part)
        if len(text) > 2000:
            text = text[:2000]
        episodes, receipt = self.ingestor.ingest(
            person,
            [
                {
                    "text": text,
                    "source": "import",
                    "origin": "knowledge",
                    "library": "knowledge",
                    "evidence_id": content_id,
                }
            ],
        )
        if not episodes:
            return _err("save_knowledge_collection", f"rejected:{receipt.rejected}")
        tags = episodes[0].tags
        batch = _batch(
            person,
            (
                MemoryProposal(
                    op=OpKind.ADD,
                    layer=MemoryLayer.ASSET,
                    text=text,
                    evidence=(content_id,),
                    reason="tool_collection",
                    tags=tags,
                ),
            ),
        )
        committed = self.sculptor.apply(batch, allowed_evidence={content_id})
        if committed.rejected:
            return _err("save_knowledge_collection", f"rejected:{committed.rejected}")
        return _ok(
            "save_knowledge_collection",
            {"entry_id": committed.committed[0], "content_id": content_id, "tags": tags.model_dump()},
        )

    def delete_knowledge_collection(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        content_id = args.get("content_id", "").strip()
        if content_id:
            ids = tuple(
                e.entry_id
                for e in self.store.entries(person, ["asset"])
                if content_id in e.evidence or e.entry_id == content_id
            )
            scope = PurgeScope(person_id=person, entry_ids=ids)
        else:
            scope = PurgeScope(person_id=person, layers=(MemoryLayer.ASSET,))
        receipt = self.store.purge(scope)
        return _ok("delete_knowledge_collection", receipt.model_dump())

    def export_knowledge_collections(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        fmt = args.get("format", "json").lower()
        entries = self.store.entries(person, ["asset"])
        rows = [
            {
                "entry_id": e.entry_id,
                "text": e.text,
                "evidence": list(e.evidence),
                "valid_from": e.valid_from.isoformat(),
            }
            for e in entries
        ]
        if fmt == "markdown":
            content = "\n".join(f"- ({r['valid_from']}) {r['text']}" for r in rows) or "_empty_"
        else:
            content = json.dumps(rows, ensure_ascii=False)
        return _ok("export_knowledge_collections", {"format": fmt, "entries": len(rows), "content": content})

    def link_memory_to_collection(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        memory_id = _text(args, "memory_id")
        collection_id = _text(args, "collection_id")
        memory = next((e for e in self.store.entries(person) if e.entry_id == memory_id), None)
        if memory is None:
            return _err("link_memory_to_collection", "memory_not_found")
        collection = next(
            (
                e
                for e in self.store.entries(person, ["asset"])
                if e.entry_id == collection_id or collection_id in e.evidence
            ),
            None,
        )
        if collection is None:
            return _err("link_memory_to_collection", "collection_not_found")
        record = self.store.add_link(
            person,
            memory_id=memory_id,
            collection_id=collection_id,
            note=args.get("note", ""),
            created_at=date.today().isoformat(),  # noqa: DTZ011 —— 记忆链接按本地日历日
        )
        return _ok("link_memory_to_collection", record)

    def fetch_url_content(self, args: Mapping[str, str]) -> ToolResult:
        url = _text(args, "url")
        try:
            _assert_public_http_url(url)
        except ValueError as exc:
            return _err("fetch_url_content", str(exc))
        if self.fetch is None:
            return _err("fetch_url_content", "fetch_adapter_unavailable")
        content = str(self.fetch(url))
        if len(content) > 20000:
            content = content[:20000]
        return _ok("fetch_url_content", {"url": url, "chars": len(content), "content": content})

    # -- 5 read/stat ---------------------------------------------------
    def search_memories(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        q = _text(args, "query").lower()
        limit = _limit(args)
        rows = [
            e
            for e in self.store.entries(person)
            if e.tags.library != "knowledge" and (q in e.text.lower() or q in " ".join(e.evidence).lower())
        ]
        return _ok("search_memories", {"query": q, "hits": _entry_summaries(rows[:limit])})

    def search_knowledge_collections(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        q = _text(args, "query").lower()
        limit = _limit(args)
        rows = [
            e for e in self.store.entries(person, ["asset"]) if q in e.text.lower() or q in " ".join(e.evidence).lower()
        ]
        return _ok("search_knowledge_collections", {"query": q, "hits": _entry_summaries(rows[:limit])})

    def get_recent_memories(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        rows = sorted(self.store.entries(person), key=lambda e: (e.valid_from, e.recorded_at), reverse=True)
        return _ok("get_recent_memories", {"hits": _entry_summaries(rows[: _limit(args)])})

    def get_memory_stats(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        entries = self.store.entries(person)
        by_layer: dict[str, int] = {}
        by_library: dict[str, int] = {}
        by_category: dict[str, int] = {}
        for entry in entries:
            by_layer[entry.layer.value] = by_layer.get(entry.layer.value, 0) + 1
            by_library[entry.tags.library or "(none)"] = by_library.get(entry.tags.library or "(none)", 0) + 1
            if entry.tags.category:
                by_category[entry.tags.category] = by_category.get(entry.tags.category, 0) + 1
        return _ok(
            "get_memory_stats",
            {"total": len(entries), "by_layer": by_layer, "by_library": by_library, "by_category": by_category},
        )

    def get_knowledge_summary(self, args: Mapping[str, str]) -> ToolResult:
        person = _person(args)
        budget = int(args.get("budget_chars", "600") or 600)
        digest = build_digest(self.store.entries(person, ["asset"]), person, min(max(budget, 100), 2000))
        return _ok(
            "get_knowledge_summary",
            {
                "state": digest.state,
                "chars": digest.chars,
                "sections": [{"name": s.name, "text": s.text} for s in digest.sections],
            },
        )


def _batch(person: str, proposals: tuple[MemoryProposal, ...]) -> ProposalBatch:
    payload = "|".join(f"{p.op.value}:{p.layer.value}:{p.text}:{','.join(p.evidence)}" for p in proposals)
    digest = hashlib.sha256(f"{person}|{payload}".encode()).hexdigest()[:24]
    return ProposalBatch(person_id=person, batch_hash=digest, proposals=proposals)


def _person(args: Mapping[str, str]) -> str:
    value = str(args.get("person_id") or "").strip()
    if not value:
        raise ValueError("person_id_required")
    return value[:64]


def _text(args: Mapping[str, str], key: str) -> str:
    value = str(args.get(key) or "").strip()
    if not value:
        raise ValueError(f"{key}_required")
    return value


def _list(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in re.split(r"[,，、]", value) if part.strip())
    if isinstance(value, (list, tuple)):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def _limit(args: Mapping[str, str]) -> int:
    try:
        return min(max(int(args.get("limit", "5") or 5), 1), 20)
    except (TypeError, ValueError):
        return 5


def _entry_summaries(rows: list[MemoryEntry]) -> list[dict[str, Any]]:
    return [
        {
            "entry_id": e.entry_id,
            "layer": e.layer.value,
            "text": e.text[:300],
            "valid_from": e.valid_from.isoformat(),
            "tags": e.tags.model_dump(),
        }
        for e in rows
    ]


def _ok(name: str, payload: object) -> ToolResult:
    return ToolResult(name=name, ok=True, content=json.dumps(payload, ensure_ascii=False))


def _err(name: str, reason: str) -> ToolResult:
    return ToolResult(name=name, ok=False, reason=reason, content="")


def _stable_id(prefix: str, *parts: str) -> str:
    return f"{prefix}_{hashlib.sha256('|'.join(parts).encode('utf-8')).hexdigest()[:16]}"


def _assert_public_http_url(url: str) -> None:
    if not re.match(r"^https?://", url, flags=re.IGNORECASE):
        raise ValueError("url_scheme_not_allowed")
    authority = url.split("://", 1)[1].split("/", 1)[0]
    host = authority.rsplit("@", 1)[-1].split(":", 1)[0].strip().lower()
    if not host:
        raise ValueError("url_host_required")
    if (
        host in {"localhost", "::1", "0.0.0.0"}  # noqa: S104 —— 这是 SSRF 拒绝名单，不是绑定地址
        or host.startswith(("127.", "10.", "192.168.", "169.254."))
        or re.match(r"172\.(1[6-9]|2\d|3[01])\.", host)
    ):
        raise ValueError("url_private_address")
