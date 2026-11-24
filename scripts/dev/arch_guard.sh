#!/usr/bin/env bash
# Architecture guard: one command for the three structural gates.
# Usage: scripts/dev/arch_guard.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT/agent"

echo "[1/3] import boundary (AST)"
PYTHONPATH=src .venv312/bin/python -m pytest tests/contract/test_import_boundary.py tests/agentv3/test_boundary.py -q

echo "[2/3] shared contracts + L2 ports (backend)"
cd "$ROOT/backend"
PYTHONPATH=. .venv312/bin/python -m pytest tests/agent_contract -q

echo "[3/3] forbidden legacy import / agent package hotline"
cd "$ROOT/agent"
PYTHONPATH=src .venv312/bin/python - <<'PY'
import ast
from pathlib import Path

src = Path("src")
bad = []
for path in src.rglob("*.py"):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] == "agentv3":
                    bad.append(f"{path}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom) and (node.module or "").split(".")[0] == "agentv3":
            bad.append(f"{path}: from {node.module}")
# Agent package must not carry the hotline number (G8); backend L3 owns it.
for path in src.rglob("*.py"):
    if "12356" in path.read_text(encoding="utf-8"):
        bad.append(f"{path}: hotline literal")
if bad:
    raise SystemExit(f"legacy import/marker found: {bad}")
print("legacy imports: none; hotline literals: none")
PY

echo "arch guard: PASS"
