"""L3 agent endpoints: digest never 4xx on missing profile; echo is read-only.

Uses dependency overrides so no database is needed. The point is the contract
behaviour, not SQL correctness (that has its own tests).
"""

from __future__ import annotations

import sys
from contextlib import nullcontext
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.deps import agent_service_user  # noqa: E402
from app.db.models import ConsentGrant, User  # noqa: E402
from app.db.session import get_db  # noqa: E402
from app.main import create_app  # noqa: E402


class _Result:
    def __init__(self, value):
        self._value = value

    def scalar_one_or_none(self):
        return self._value

    def scalars(self):
        return self

    def all(self):
        return []


class _DB:
    def __init__(self, profile=None, *, granted=False):
        self._profile = profile
        self._granted = granted
        self.added = []
        self.committed = False

    def execute(self, statement):
        if statement.column_descriptions[0]["entity"] is ConsentGrant:
            return _Result(ConsentGrant(state="granted") if self._granted else None)
        return _Result(self._profile)

    def get(self, model, entity_id):
        return None

    def scalar(self, statement):
        return 0

    def begin_nested(self):
        return nullcontext()

    def add(self, row):
        self.added.append(row)

    def flush(self):
        for row in self.added:
            if getattr(row, "id", None) is None:
                import uuid

                row.id = str(uuid.uuid4())

    def commit(self):
        self.committed = True

    def refresh(self, row):
        return None


def _client(db=None):
    app = create_app()
    user = User(id="00000000-0000-0000-0000-000000000001", status=1)
    app.dependency_overrides[get_db] = lambda: db if db is not None else _DB()
    app.dependency_overrides[agent_service_user] = lambda: user
    return TestClient(app)


def test_digest_missing_profile_is_200_unavailable():
    response = _client(_DB(granted=True)).get("/api/v1/profile/agent/digest", params={"person_id": "p1"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "unavailable"
    assert "不要编造" in data["text"]


def test_persons_create_uses_user_confirmed_epistemic():
    db = _DB(granted=True)
    response = _client(db).post(
        "/api/v1/persons/agent",
        json={"idempotency_key": "key-00000001", "display_name": "小林"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["person_id"]
    assert data["display_name"] == "小林"
    assert db.added[0].epistemic == "user_confirmed"


def test_echo_returns_no_candidate_without_writing():
    db = _DB()
    response = _client(db).post(
        "/api/v1/echo/agent",
        json={"person_id": "p1", "local_date": "2026-11-23", "limit": 1},
    )
    assert response.status_code == 200
    assert response.json()["data"]["suppressed"] == "no_candidate"
    assert not db.added and not db.committed


def test_agent_endpoints_are_not_in_openapi():
    paths = _client().get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "/agent" in p]
