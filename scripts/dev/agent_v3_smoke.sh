#!/usr/bin/env bash
# L0 compose smoke: verify agent-v3 can start and answer /v1/health without a DB.
#
# Full compose (PostgreSQL/Redis/Qdrant/MinIO) is documented in
# deploy/compose.agent-v3.yml. This script only proves the service process is
# runnable on this machine; it does not claim the L0 containers are up.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/agent"

if [ ! -x ".venv312/bin/python" ]; then
  echo "agent venv missing: .venv312 (environment error, not a pass)" >&2
  exit 2
fi

EXPECTED="${AGENT_SMOKE_EXPECT:-pass}"
PYTHONPATH=src .venv312/bin/python - <<'PY'
from fastapi.testclient import TestClient
from adapters.fakes import FakeEvents, FakeLLM, FakeSearch, _hit
from api.routes import Container, create_app
from harness.orchestrator import Orchestrator
from memory.ledger import MemoryStore

store = MemoryStore()
orch = Orchestrator(
    llm=FakeLLM(["我在听。"]),
    search=FakeSearch([_hit("e1", "西湖")]),
    events=FakeEvents(),
    store=store,
)
client = TestClient(create_app(Container(orchestrator=orch, store=store)))
health = client.get("/v1/health")
assert health.status_code == 200, health.text
assert health.json()["status"] == "ok"
chat = client.post("/v1/chat", json={"person_id": "smoke", "text": "你好"})
assert chat.status_code == 200, chat.text
assert chat.json()["reply"]
print("agent-v3 smoke: health=ok chat=ok")
PY

if [ "$EXPECTED" = "blocked" ]; then
  echo "AGENT_SMOKE_EXPECT=blocked but the service ran; update the report, not the expectation" >&2
  exit 1
fi
