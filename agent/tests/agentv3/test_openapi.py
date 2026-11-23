"""OpenAPI snapshot: paths and contract fields cannot drift silently.

The snapshot is a checked-in golden file. Changing a field or a route requires
changing the golden in the same commit, which makes drift a review event.
"""

import json
from datetime import datetime
from pathlib import Path

from agentv3.adapters.fakes import FakeClock, FakeEvents, FakeLLM, FakeSearch, _hit
from agentv3.api.routes import Container, create_app
from agentv3.memory.ledger import MemoryStore
from agentv3.orchestrator import Orchestrator

GOLDEN = Path(__file__).with_name("golden") / "openapi_snapshot.json"


def _app():
    store = MemoryStore()
    orchestrator = Orchestrator(
        llm=FakeLLM(["我在听。"]),
        search=FakeSearch([_hit("e1", "西湖")]),
        events=FakeEvents(),
        store=store,
        clock=FakeClock(datetime(2026, 11, 23, 10, 0)),
    )
    return create_app(Container(orchestrator=orchestrator, store=store))


def _snapshot() -> dict:
    spec = _app().openapi()
    schemas = spec.get("components", {}).get("schemas", {})
    return {
        "paths": {path: sorted(ops) for path, ops in spec["paths"].items()},
        "schemas": {name: sorted(props) for name, props in schemas.items()},
        "required": {name: sorted(schema.get("required", [])) for name, schema in schemas.items()},
    }


def test_openapi_matches_golden():
    assert _snapshot() == json.loads(GOLDEN.read_text(encoding="utf-8"))
