"""L2 ports: exact 15, Protocol-only, three-state DTOs, no transport imports.

The import-scan is AST-based so a comment mentioning SQLAlchemy does not fail
the test, but an actual import does.
"""

from __future__ import annotations

import ast
from pathlib import Path

from app.ports import PORTS as PORT_EXPORTS

PORTS_DIR = Path(__file__).resolve().parents[2] / "app" / "ports"
TRANSPORTS = {"sqlalchemy", "qdrant_client", "httpx", "redis", "psycopg", "requests"}


def test_exactly_fifteen_ports_are_exported():
    assert len(PORT_EXPORTS) == 15, PORT_EXPORTS


def test_every_export_is_a_protocol_or_dto():
    from app import ports

    for name in PORT_EXPORTS:
        obj = getattr(ports, name)
        assert obj is not None, name


def test_ports_do_not_import_transports():
    for path in PORTS_DIR.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert alias.name.split(".")[0] not in TRANSPORTS, (path.name, alias.name)
            elif isinstance(node, ast.ImportFrom) and node.module:
                assert node.module.split(".")[0] not in TRANSPORTS, (path.name, node.module)


def test_failure_states_are_not_booleans():
    from app.ports.memory import MemoryStatus
    from app.ports.search import SearchStatus

    assert "unavailable" in MemoryStatus.__args__
    assert "degraded" in SearchStatus.__args__
    assert "ok" in MemoryStatus.__args__
