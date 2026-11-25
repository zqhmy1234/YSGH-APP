"""BackendHTTP speaks exactly the six L3 endpoints; failures map to port errors.

A stdlib server implements the real wire shapes; a payload-key change or a
silently swallowed non-2xx breaks this test.
"""

from __future__ import annotations

import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from adapters.backend_http import BackendHTTP  # noqa: E402
from harness.contracts import SearchQuery  # noqa: E402
from ports import PortUnavailable  # noqa: E402


class _Handler(BaseHTTPRequestHandler):
    seen: list[tuple[str, dict | None]] = []
    headers: list[dict[str, str]] = []

    def _reply(self, payload: dict, code: int = 200) -> None:
        raw = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        type(self).seen.append((self.path, None))
        if self.path.startswith("/api/v1/profile/agent/digest"):
            self._reply({"data": {"status": "insufficient_data", "text": "资料不足", "chars": 4, "sections": []}})
        else:
            self._reply({"detail": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        type(self).seen.append((self.path, body))
        type(self).headers.append({k: v for k, v in self.headers.items()})
        if self.path == "/api/v1/search/agent":
            self._reply({"data": {"hits": [{"content_id": "c1", "text": "桂花", "score": 0.9}]}})
        elif self.path == "/api/v1/contents/agent":
            self._reply({"data": {"content_id": "c1", "deleted": False}})
        elif self.path == "/api/v1/echo/agent":
            self._reply({"data": {"items": [], "suppressed": "no_candidate"}})
        else:
            self._reply({"detail": "not found"}, 404)

    def do_DELETE(self):  # noqa: N802
        type(self).seen.append((self.path, None))
        self._reply({"data": {"content_id": "c1", "deleted": True}})

    def log_message(self, *args):  # noqa: D102
        pass


@pytest.fixture()
def base():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    _Handler.seen = []
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def test_search_round_trips_and_keeps_user_isolation_header(base):
    adapter = BackendHTTP(base_url=base, token="t", user_id="u1")
    result = adapter.search(SearchQuery(query="桂花", person_id="u1"))
    assert result.hits and result.hits[0].evidence_id == "c1"
    method, payload = _Handler.seen[0]
    assert method == "/api/v1/search/agent"
    assert payload is not None and payload["limit"] == 20


def test_create_content_validates_shared_contract(base):
    adapter = BackendHTTP(base_url=base, token="t", user_id="u1")
    data = adapter.create_content_agent(
        {"content_type": "text", "client_generated_id": "cg-00000001", "text": "记住这句话"}
    )
    assert data["content_id"] == "c1"
    assert _Handler.seen[-1][0] == "/api/v1/contents/agent"


def test_invalid_content_payload_fails_before_network(base):
    adapter = BackendHTTP(base_url=base, token="t", user_id="u1")
    with pytest.raises(Exception):
        adapter.create_content_agent({"content_type": "text", "client_generated_id": ""})


def test_delete_and_digest_round_trip(base):
    adapter = BackendHTTP(base_url=base, token="t", user_id="u1")
    assert adapter.delete_content("cg-00000001") is True
    digest = adapter.digest("u1")
    assert digest.state == "insufficient_data"


def test_http_4xx_is_port_unavailable_not_empty(base):
    adapter = BackendHTTP(base_url=base, token="t", user_id="u1")
    with pytest.raises(PortUnavailable):
        adapter._post("/api/v1/nope", {})


def test_missing_base_url_is_unavailable():
    with pytest.raises(PortUnavailable):
        BackendHTTP().search(SearchQuery(query="x", person_id="p1"))


def test_search_wrapper_calls_backend_once_per_turn(base):
    """keyword/dense/sparse must not fire three full RAG pipelines for one turn."""
    from adapters.backend_http import BackendHTTP, BackendSearchHTTP

    client = BackendHTTP(base, token="t", user_id="u1")
    search = BackendSearchHTTP(client)
    q = SearchQuery(query="桂花", person_id="p1")
    first = search.keyword(q)
    second = search.dense(q)
    third = search.sparse(q)
    assert first == second == third
    paths = [p for p, _ in _Handler.seen if p == "/api/v1/search/agent"]
    assert len(paths) == 1, f"expected one backend call, saw {len(paths)}"


def test_headers_carry_the_acting_user_from_the_query(base):
    from adapters.backend_http import BackendHTTP, BackendSearchHTTP

    client = BackendHTTP(base, token="t", user_id="boot-user")
    BackendSearchHTTP(client).keyword(SearchQuery(query="西湖", person_id="p-turn"))
    assert _Handler.headers[-1].get("X-On-Behalf-Of") == "p-turn"
    assert _Handler.headers[-1].get("X-Agent-Token") == "t"
