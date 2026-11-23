"""Import boundary: arrows point one way and transports never leave ``adapters``.

This is the executable version of the layering rule in AGENTS/plan §0.3:

    api -> harness -> agents/memory -> ports -> harness.contracts
    adapters -> ports/contracts
    platform_support -> stdlib + fastapi only

A new hard import between siblings goes red before code review can miss it.
"""

from __future__ import annotations

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
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
            out.extend(alias.name for alias in node.names)
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


def _files(*parts: str) -> list[Path]:
    base = SRC.joinpath(*parts)
    return sorted(p for p in base.rglob("*.py") if "__pycache__" not in p.parts)


def test_contracts_depend_on_nothing_internal():
    for module in _imports(SRC / "harness" / "contracts.py"):
        assert module.split(".")[0] not in INTERNAL_ROOTS, f"contracts must stay pure: {module}"


def test_ports_depend_only_on_contracts():
    allowed = {"harness.contracts", "typing", "collections.abc", "datetime", "__future__"}
    for module in _imports(SRC / "ports" / "__init__.py"):
        assert module in allowed, module


def test_agents_never_import_each_other():
    for path in _files("agents"):
        if path.name == "__init__.py":
            continue
        own = f"agents.{path.stem}"
        for module in _imports(path):
            assert not (module.startswith("agents.") and module != own), f"{path.name} imports {module}"


def test_memory_never_imports_agents_adapters_api_or_orchestrator():
    for path in _files("memory"):
        for module in _imports(path):
            assert not module.startswith(("agents", "adapters", "api", "harness.orchestrator")), (
                f"{path.name} -> {module}"
            )


def test_adapters_never_import_agents_or_orchestrator():
    for path in _files("adapters"):
        for module in _imports(path):
            assert not module.startswith(("agents", "api", "harness.orchestrator")), (
                f"{path.relative_to(SRC)} -> {module}"
            )


def test_orchestrator_is_the_only_agent_composition_root():
    for path in _files("harness"):
        if path.name in {"orchestrator.py", "__init__.py"} or path.parent.name == "domain":
            continue
        for module in _imports(path):
            assert not module.startswith("agents"), f"{path.name} composes agents"


def test_transports_only_in_adapters():
    for path in _files():
        if "adapters" in path.parts or "judge" in path.parts:
            continue
        for module in _imports(path):
            top = module.split(".")[0]
            assert top not in TRANSPORTS, f"{path.relative_to(SRC)} imports transport {module}"


def test_every_agent_module_declares_a_docstring():
    for path in _files("agents"):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert ast.get_docstring(tree), f"{path.name} has no module docstring"


def test_no_module_imports_legacy_agentv3():
    for path in _files():
        for module in _imports(path):
            assert not module.startswith("agentv3"), f"{path.relative_to(SRC)} still imports legacy {module}"
