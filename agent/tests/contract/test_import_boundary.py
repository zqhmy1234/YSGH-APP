"""Import boundary, contract-test entry (plan DoD path)."""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import ast  # noqa: E402

ROOT = SRC / "agentv3"
TRANSPORTS = {"sqlite3", "urllib", "httpx", "requests", "openai", "psycopg", "redis", "qdrant_client"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = ".".join(path.relative_to(SRC).with_suffix("").parts).split(".")[:-1]
                root = parts[: len(parts) - (node.level - 1)]
                out.append(".".join([*root, node.module] if node.module else root))
            elif node.module:
                out.append(node.module)
    return out


def test_agents_do_not_import_each_other():
    for path in (ROOT / "agents").glob("*.py"):
        if path.name == "__init__.py":
            continue
        own = f"agentv3.agents.{path.stem}"
        for module in _imports(path):
            assert not (module.startswith("agentv3.agents.") and module != own), (path.name, module)


def test_transports_are_adapter_only():
    for path in ROOT.rglob("*.py"):
        if "adapters" in path.parts:
            continue
        for module in _imports(path):
            assert module.split(".")[0] not in TRANSPORTS, (path.name, module)


def test_contracts_are_pure():
    for module in _imports(ROOT / "contracts.py"):
        assert not module.startswith("agentv3")
