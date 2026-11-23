"""Real localhost HTTP round-trip for the production adapters.

No backend process is needed: a stdlib server implements the exact wire shapes
the adapters speak, so a change in payload keys or parsing fails here.
"""

from __future__ import annotations

import json
import threading
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from agentv3.adapters.backend import BackendEvents, BackendLLM, BackendSearch
from agentv3.contracts import LLMRequest, SearchQuery
from agentv3.ports import PortUnavailable


class _Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict]] = []

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        type(self).seen.append((self.path, body))
        if self.path == "/internal/rag/search":
            payload = {"items": [{"evidence_id": "ev-1", "title": "桂花", "content": "她喜欢桂花", "score": 0.9, "kind": "content"}]}
        elif self.path == "/internal/events/hits":
            payload = {"items": [{"event_id": "e-1", "title": "秋天", "day": "2025-10-01", "summary": "桂花开了", "score": 0.8}]}
        elif self.path == "/chat/completions":
            payload = {"choices": [{"message": {"content": "我在", "tool_calls": []}}]}
        elif self.path == "/slow":
            self.send_error(504)
            return
        else:
            self.send_error(404)
            return
        raw = json.dumps(payload).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):
        pass


@pytest.fixture()
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen = []
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_search_three_channels_and_events_really_round_trip(base):
    search = BackendSearch(base_url=base, token="t")
    q = SearchQuery(query="桂花", person_id="p1")
    assert search.keyword(q, alternates=("桂花香",))[0].evidence_id == "ev-1"
    assert search.dense(q)[0].content == "她喜欢桂花"
    assert search.sparse(q)[0].channel == "sparse"
    assert search.window(q)[0].channel == "time"
    events = BackendEvents(base_url=base, token="t").hits("p1", from_=date(2025, 1, 1), to=None, keywords=("桂花",))
    assert events[0].event_id == "e-1"
    paths = [p for p, _ in _Handler.seen]
    assert "/internal/rag/search" in paths and "/internal/events/hits" in paths


def test_llm_parses_content_and_tool_calls(base):
    resp = BackendLLM(base_url=base, api_key="k", model="qwen-flash").complete(
        LLMRequest(system="s", user="u", tools=("search",))
    )
    assert resp.text == "我在" and resp.finish == "stop"


def test_missing_config_is_port_unavailable():
    with pytest.raises(PortUnavailable):
        BackendSearch(base_url="", token="").dense(SearchQuery(query="x", person_id="p"))
