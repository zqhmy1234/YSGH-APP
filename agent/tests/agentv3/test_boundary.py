"""Boundary: import arrows point one way, and transports live only in adapters.

This test is the executable version of "高内聚、低耦合": if someone adds a hard
import between two agents, the suite goes red before the code review does.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "src" / "agentv3"

TRANSPORTS = {"sqlite3", "urllib", "httpx", "requests", "openai", "psycopg", "redis", "qdrant_client"}


def _imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if node.level:
                parts = _package(path).split(".")[:-1]
                root = parts[: len(parts) - (node.level - 1)]
                module = ".".join([*root, node.module] if node.module else root)
            out.append(module)
    return out


def _package(path: Path) -> str:
    rel = path.relative_to(ROOT.parent).with_suffix("")
    return ".".join(rel.parts)


def _files(pattern: str) -> list[Path]:
    return sorted(ROOT.glob(pattern))


def test_contracts_depend_on_nothing_internal():
    for module in _imports(ROOT / "contracts.py"):
        assert not module.startswith("agentv3"), f"contracts must stay pure: {module}"


def test_ports_depend_only_on_contracts():
    for module in _imports(ROOT / "ports.py"):
        assert module in {"agentv3.contracts", "typing", "datetime", "__future__"}, module


def test_agents_never_import_each_other():
    for path in _files("agents/*.py"):
        if path.name == "__init__.py":
            continue
        own = f"agentv3.agents.{path.stem}"
        for module in _imports(path):
            assert not (module.startswith("agentv3.agents.") and module != own), f"{path.name} imports {module}"


def test_memory_never_imports_agents_or_adapters():
    for path in _files("memory/*.py"):
        for module in _imports(path):
            assert not module.startswith(("agentv3.agents", "agentv3.adapters", "agentv3.api", "agentv3.orchestrator")), f"{path.name} -> {module}"


def test_adapters_never_import_agents_memory_or_orchestrator():
    for path in _files("adapters/*.py"):
        for module in _imports(path):
            assert not module.startswith(("agentv3.agents", "agentv3.api", "agentv3.orchestrator")), f"{path.name} -> {module}"


def test_orchestrator_is_the_only_agent_composition_root():
    for path in _files("*.py"):
        if path.name in {"orchestrator.py", "__init__.py"}:
            continue
        for module in _imports(path):
            assert not module.startswith("agentv3.agents"), f"{path.name} composes agents"


def test_transports_only_in_adapters():
    for path in _files("**/*.py"):
        if "adapters" in path.parts:
            continue
        for module in _imports(path):
            top = module.split(".")[0]
            assert top not in TRANSPORTS, f"{path.relative_to(ROOT)} imports transport {module}"


def test_every_agent_module_declares_a_docstring():
    for path in _files("agents/*.py"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert ast.get_docstring(tree), f"{path.name} has no module docstring"
