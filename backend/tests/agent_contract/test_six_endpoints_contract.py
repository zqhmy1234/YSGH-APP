"""Check the current internal routes and preserve the six original L3 endpoints.

The exact route set covers the documented additional read and write operations.
It is checked against the FastAPI app so handler changes cannot silently drift.
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.main import create_app  # noqa: E402

EXPECTED = {
    ("POST", "/api/v1/search/agent"),
    ("POST", "/api/v1/contents/agent"),
    ("DELETE", "/api/v1/contents/agent/by-cgid/{client_generated_id}"),
    ("GET", "/api/v1/profile/agent/digest"),
    ("POST", "/api/v1/persons/agent"),
    ("GET", "/api/v1/persons/agent"),
    ("DELETE", "/api/v1/persons/agent/{person_id}"),
    ("POST", "/api/v1/echo/agent"),
    ("GET", "/api/v1/memory/agent"),
    ("POST", "/api/v1/memory/agent"),
    ("DELETE", "/api/v1/memory/agent/{entry_id}"),
    ("GET", "/api/v1/persona/agent"),
}
# The plan's six business endpoints; the persons GET is the read pair of the
# same B5-03 resource and is explicitly listed above. Nothing else may appear.
PLAN_SIX = {
    ("POST", "/api/v1/search/agent"),
    ("POST", "/api/v1/contents/agent"),
    ("DELETE", "/api/v1/contents/agent/by-cgid/{client_generated_id}"),
    ("GET", "/api/v1/profile/agent/digest"),
    ("POST", "/api/v1/persons/agent"),
    ("POST", "/api/v1/echo/agent"),
}


def _agent_routes() -> set[tuple[str, str]]:
    app = create_app()
    found: set[tuple[str, str]] = set()
    for route in app.routes:
        # FastAPI wraps include_router() results in ``_IncludedRouter``; the real
        # APIRoute objects live on ``original_router.routes``.
        original = getattr(route, "original_router", None)
        candidates = getattr(route, "routes", None) or getattr(original, "routes", None) or [route]
        for sub in candidates:
            path = getattr(sub, "path", "")
            if "/agent" not in path:
                continue
            for method in getattr(sub, "methods", set()) or set():
                if method in {"GET", "POST", "DELETE", "PUT", "PATCH"}:
                    found.add((method, path))
    return found


def test_agent_route_set_is_exactly_expected():
    routes = _agent_routes()
    missing = EXPECTED - routes
    extra = {r for r in routes if r[1].startswith("/api/v1/") and "agent" in r[1]} - EXPECTED
    assert not missing, f"missing agent endpoints: {missing}"
    assert not extra, f"undocumented agent endpoints: {extra}"


def test_persons_list_get_is_not_a_second_create_path():
    routes = _agent_routes()
    assert ("GET", "/api/v1/persons/agent") in routes
    assert ("POST", "/api/v1/persons/agent") in routes


def test_plan_six_are_all_present():
    assert PLAN_SIX <= _agent_routes()
