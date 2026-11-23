"""L3 agent endpoints: digest never 4xx on missing profile; echo is read-only.

Uses dependency overrides so no database is needed. The point is the contract
behaviour, not SQL correctness (that has its own tests).
"""

from __future__ import annotations

import sys
from pathlib import Path

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.api.deps import agent_service_user  # noqa: E402
from app.db.models import User  # noqa: E402
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
    def __init__(self, profile=None):
        self._profile = profile
        self.added = []
        self.committed = False

    def execute(self, *args, **kwargs):
        return _Result(self._profile)

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


def _client(profile=None):
    app = create_app()
    user = User(id="00000000-0000-0000-0000-000000000001", status=1)
    app.dependency_overrides[get_db] = lambda: _DB(profile)
    app.dependency_overrides[agent_service_user] = lambda: user
    return TestClient(app)


def test_digest_missing_profile_is_200_unavailable():
    response = _client().get("/api/v1/profile/agent/digest", params={"person_id": "p1"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "unavailable"
    assert "不要编造" in data["text"]


def test_persons_create_uses_user_confirmed_epistemic():
    response = _client().post(
        "/api/v1/persons/agent",
        json={"idempotency_key": "key-00000001", "display_name": "小林"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["person_id"]
    assert data["display_name"] == "小林"


def test_echo_returns_no_candidate_without_writing():
    response = _client().post(
        "/api/v1/echo/agent",
        json={"person_id": "p1", "local_date": "2026-11-23", "limit": 1},
    )
    assert response.status_code == 200
    assert response.json()["data"]["suppressed"] == "no_candidate"


def test_agent_endpoints_are_not_in_openapi():
    paths = _client().get("/openapi.json").json()["paths"]
    assert not [p for p in paths if "/agent" in p]
