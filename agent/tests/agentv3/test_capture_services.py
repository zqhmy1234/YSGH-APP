from __future__ import annotations

import json

from agentv3.agents.sculptor import Sculptor
from agentv3.contracts import MemoryLayer
from agentv3.memory.ledger import MemoryStore
from agentv3.services.tools import ACTION_TOOLS, READ_TOOLS, TOOL_NAMES, MemoryToolService

P = "p1"


def _service(fetch=None):
    store = MemoryStore()
    return store, MemoryToolService(store=store, sculptor=Sculptor(store), fetch=fetch)


def test_thirteen_tools_split_8_actions_and_5_reads():
    assert len(TOOL_NAMES) == 13
    assert len(ACTION_TOOLS) == 8
    assert len(READ_TOOLS) == 5
    names = {s["function"]["name"] for s in MemoryToolService.schemas()}
    assert names == set(TOOL_NAMES)


def test_save_memory_writes_ledger_with_tags():
    store, svc = _service()
    result = svc.save_memory({"person_id": P, "text": "今天和朋友去西湖，心情很开心", "people": "朋友"})
    assert result.ok, result.reason
    payload = json.loads(result.content)
    assert payload["entry_id"]
    entry = store.entries(P)[0]
    assert entry.layer is MemoryLayer.NARRATIVE
    assert entry.tags.library == "personal"
    assert entry.tags.mood == "开心"
    assert entry.tags.category in {"事件", "人物"}


def test_save_knowledge_collection_goes_to_asset_layer():
    store, svc = _service()
    result = svc.save_knowledge_collection({"person_id": P, "title": "RAG 入门", "url": "https://example.com/rag", "summary": "检索增强"})
    assert result.ok, result.reason
    entry = store.entries(P, ["asset"])[0]
    assert entry.tags.library == "knowledge"
    assert entry.tags.image_route == ""


def test_search_and_stats_include_tags():
    store, svc = _service()
    assert svc.save_memory({"person_id": P, "text": "今天去西湖散步"}).ok
    assert svc.save_knowledge_collection({"person_id": P, "title": "西湖十景", "url": "https://example.com/xh"}).ok
    hits = json.loads(svc.search_memories({"person_id": P, "query": "西湖"}).content)["hits"]
    assert len(hits) == 1
    knowledge = json.loads(svc.search_knowledge_collections({"person_id": P, "query": "西湖"}).content)["hits"]
    assert len(knowledge) == 1
    stats = json.loads(svc.get_memory_stats({"person_id": P}).content)
    assert stats["total"] == 2
    assert stats["by_library"]["knowledge"] == 1


def test_update_summary_never_writes_ledger():
    store, svc = _service()
    result = svc.update_memory_summary({"person_id": P, "text": "最近在准备考试"})
    assert result.ok
    assert store.entries(P) == []
    assert svc.summaries[P] == "最近在准备考试"


def test_link_and_delete_collection():
    store, svc = _service()
    saved = json.loads(svc.save_memory({"person_id": P, "text": "今天去西湖"}).content)
    coll = json.loads(svc.save_knowledge_collection({"person_id": P, "title": "西湖", "url": "https://example.com/x"}).content)
    linked = svc.link_memory_to_collection({"person_id": P, "memory_id": saved["entry_id"], "collection_id": coll["entry_id"]})
    assert linked.ok
    assert len(store.links(P)) == 1
    deleted = svc.delete_knowledge_collection({"person_id": P, "content_id": coll["content_id"]})
    assert deleted.ok
    assert store.entries(P, ["asset"]) == []


def test_export_markdown():
    store, svc = _service()
    svc.save_knowledge_collection({"person_id": P, "title": "西湖", "url": "https://example.com/x"})
    result = svc.export_knowledge_collections({"person_id": P, "format": "markdown"})
    assert result.ok
    assert "西湖" in json.loads(result.content)["content"]


def test_fetch_uses_injected_adapter_and_blocks_private_urls():
    _, svc = _service(fetch=lambda url: "正文" * 10)
    assert svc.fetch_url_content({"url": "https://example.com/a"}).ok
    private = svc.fetch_url_content({"url": "http://127.0.0.1/admin"})
    assert not private.ok
    with_private = svc.fetch_url_content({"url": "http://127.0.0.1/admin"})
    assert not with_private.ok


def test_delete_memory_purges_entry():
    store, svc = _service()
    saved = json.loads(svc.save_memory({"person_id": P, "text": "今天去西湖"}).content)
    deleted = svc.delete_memory({"person_id": P, "entry_id": saved["entry_id"]})
    assert deleted.ok
    assert store.entries(P) == []


def test_delete_does_not_touch_other_person():
    store, svc = _service()
    mine = json.loads(svc.save_memory({"person_id": P, "text": "今天去西湖"}).content)
    other = json.loads(svc.save_memory({"person_id": "p2", "text": "今天去西湖"}).content)
    svc.delete_memory({"person_id": P, "entry_id": mine["entry_id"]})
    assert store.entries(P) == []
    assert len(store.entries("p2")) == 1
    assert store.entries("p2")[0].entry_id == other["entry_id"]
