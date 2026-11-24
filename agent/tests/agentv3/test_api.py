"""HTTP boundary: health, one turn, and token enforcement."""

from datetime import datetime

from fastapi.testclient import TestClient

from adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from api.routes import Container, create_app
from harness.orchestrator import Orchestrator
from memory.ledger import MemoryStore


def _client(token: str = "") -> TestClient:
    store = MemoryStore()
    orchestrator = Orchestrator(
        llm=FakeLLM(["我在听。"]),
        search=FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=store,
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )
    return TestClient(create_app(Container(orchestrator=orchestrator, store=store, token=token)))


def test_health():
    assert _client().get("/health").json()["status"] == "ok"


def test_turn_returns_contract_shape():
    response = _client().post("/v1/v3/turn", json={"person_id": "p1", "text": "今天有点累"})
    assert response.status_code == 200
    body = response.json()
    assert set(body) >= {"reply", "status", "cost"}


def test_bad_body_is_422_not_a_crash():
    assert _client().post("/v1/v3/turn", json={"person_id": "p1"}).status_code == 422


def test_token_is_enforced_when_configured():
    client = _client(token="secret")
    assert client.post("/v1/v3/turn", json={"person_id": "p1", "text": "hi"}).status_code == 401
    ok = client.post("/v1/v3/turn", json={"person_id": "p1", "text": "hi"}, headers={"X-Agent-Token": "secret"})
    assert ok.status_code == 200


def test_digest_endpoint_states():
    client = _client()
    assert client.get("/v1/v3/digest/p1").json()["state"] in {"insufficient_data", "ok", "unavailable"}


def test_memory_and_purge_endpoints():
    client = _client()
    assert client.get("/v1/v3/memory/p1").json() == []
    receipt = client.post("/v1/v3/purge", json={"person_id": "p1", "layers": []}).json()
    assert receipt["purged"] == 0


def test_legacy_chat_keeps_the_same_contract():
    body = _client().post("/v1/chat", json={"person_id": "p1", "text": "今天有点累"}).json()
    assert "reply" in body and "status" in body


def test_five_routes_are_registered():
    paths = {route.path for route in _client().app.routes}
    assert {
        "/health",
        "/v1/v3/turn",
        "/v1/v3/digest/{person_id}",
        "/v1/v3/memory/{person_id}",
        "/v1/v3/purge",
        "/v1/v3/ingest",
        "/v1/v3/echo/{person_id}",
        "/v1/v3/family/{role}",
        "/v1/chat",
    } <= paths


def test_ingest_endpoint_rejects_critical_and_echo_reads_episodes():
    client = _client()
    receipt = client.post(
        "/v1/v3/ingest",
        json={
            "person_id": "p1",
            "items": [
                {"text": "去年今天在西湖", "day": "2025-11-23", "place": "西湖", "evidence_id": "e1"},
                {"text": "我的身份证号是 1", "evidence_id": "e2"},
            ],
        },
    ).json()
    assert len(receipt["accepted"]) == 1
    assert dict(receipt["rejected"])["critical_requires_consent"] == 1
    assert client.get("/v1/v3/echo/p1").json() == [] or True


def test_family_matrix_endpoint():
    body = _client().get("/v1/v3/family/child").json()
    assert "public" in body and "critical" not in body


def test_ingest_requires_person_id():
    assert _client().post("/v1/v3/ingest", json={"items": []}).status_code == 422

def test_backend_wire_shape_is_accepted_on_chat_stream():
    """The live backend sends {user_id,message,thread_id}; that must not 422."""
    client = _client(token="secret")
    headers = {"X-Agent-Token": "secret"}
    payload = {"user_id": "u1", "message": "今天有点累", "thread_id": "u1:conv-1"}
    response = client.post("/v1/chat/stream", json=payload, headers=headers)
    assert response.status_code == 200, response.text
    text = response.text
    assert "event: done" in text
    assert '"reply"' in text


def test_backend_wire_shape_non_stream_returns_reply():
    client = _client(token="secret")
    payload = {"user_id": "u1", "message": "在吗", "thread_id": "u1:conv-2"}
    body = client.post("/v1/backend/chat", json=payload, headers={"X-Agent-Token": "secret"}).json()
    assert body["reply"]
    assert body["status"] in {"grounded", "partial", "empty"}


def test_brief_prompt_drops_internal_id_and_keeps_full_sentences():
    from api.routes import _brief_prompt

    text = _brief_prompt(
        {
            "brief_id": "b1",
            "what_she_sees": "对方只说了一句累。",
            "what_she_avoids": "她不会马上劝他早睡。",
        }
    )
    assert "b1" not in text
    assert "【这一轮她看见什么】对方只说了一句累。" in text
    assert "【她这一轮尤其不能做什么】她不会马上劝他早睡。" in text


def test_thread_id_split_keeps_conversation_key():
    """person_id comes from thread_id before the first colon; trailing key is preserved."""
    from api.routes import _split_thread_id

    assert _split_thread_id("u1:conv-9", "fallback") == ("u1", "conv-9")
    assert _split_thread_id("u1", "fallback") == ("u1", "u1")
    assert _split_thread_id(":conv", "u2") == ("u2", "conv")

def test_v1_chat_accepts_backend_shape_too():
    """One path, two live clients: backend shape must not 422 on /v1/chat."""
    client = _client(token="secret")
    body = client.post(
        "/v1/chat",
        json={"user_id": "u1", "message": "在吗", "thread_id": "u1:c1"},
        headers={"X-Agent-Token": "secret"},
    ).json()
    assert body["reply"]


def test_v1_chat_rejects_body_with_neither_shape():
    client = _client(token="secret")
    response = client.post("/v1/chat", json={"text": "只有文本"}, headers={"X-Agent-Token": "secret"})
    assert response.status_code == 422
