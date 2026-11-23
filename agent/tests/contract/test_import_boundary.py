"""Import boundary, contract-test entry (plan DoD path).

The AST scan is the authority; ``grep`` is never used to decide dependency
direction. Rules: contracts pure, ports only contracts, no agent<->agent import,
transports only in adapters, no imports of the archived legacy package.
"""

import ast
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

TRANSPORTS = {"sqlite3", "urllib", "httpx", "requests", "openai", "psycopg", "redis", "qdrant_client", "litellm"}
INTERNAL_ROOTS = {
    "adapters",
    "agents",
    "api",
    "harness",
    "jobs",
    "judge",
    "memory",
    "platform_support",
    "ports",
    "prompts",
    "tools",
}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                rel = path.relative_to(SRC).with_suffix("")
                parts = list(rel.parts)[:-1]
                keep = len(parts) - (node.level - 1)
                base = parts[: max(0, keep)]
                module = ".".join([*base, node.module] if node.module else base)
            if module:
                out.append(module)
    return out


def test_agents_do_not_import_each_other():
    for path in (SRC / "agents").glob("*.py"):
        if path.name == "__init__.py":
            continue
        own = f"agents.{path.stem}"
        for module in _imports(path):
            assert not (module.startswith("agents.") and module != own), (path.name, module)


def test_transports_are_adapter_only():
    for path in SRC.rglob("*.py"):
        if "adapters" in path.parts or "judge" in path.parts:
            continue
        for module in _imports(path):
            assert module.split(".")[0] not in TRANSPORTS, (path.name, module)


def test_contracts_are_pure():
    for module in _imports(SRC / "harness" / "contracts.py"):
        assert module.split(".")[0] not in INTERNAL_ROOTS, module


def test_ports_only_depend_on_contracts():
    allowed = {"harness.contracts", "typing", "collections.abc", "datetime", "__future__"}
    for module in _imports(SRC / "ports" / "__init__.py"):
        assert module in allowed, module


def test_no_legacy_agentv3_imports():
    for path in SRC.rglob("*.py"):
        for module in _imports(path):
            assert not module.startswith("agentv3"), f"{path.relative_to(SRC)} imports {module}"
