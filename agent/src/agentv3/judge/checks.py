"""Static gates the judge runs before any dynamic test.

Each check returns findings, never raises: the judge decides severity. A check
that cannot read a file reports P2 (environment) instead of pretending to pass.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "agentv3"
REPO = Path(__file__).resolve().parents[4]

FORBIDDEN_PHRASES = (
    "已联络紧急联系人",
    "已经通知了家属",
    "skip existing library",
    "保证不会发生",
)

PLACEHOLDERS = ("NotImplementedError", "TODO", "FIXME", "pass  # stub")


@dataclass(frozen=True)
class Finding:
    check: str
    severity: str  # P0 blocks, P1 should fix, P2 environment
    detail: str
    repro: str = ""

    def as_dict(self) -> dict[str, str]:
        return {"check": self.check, "severity": self.severity, "detail": self.detail, "repro": self.repro}


RULEBOOK = {"agentv3.judge.checks"}


def _sources() -> list[Path]:
    return sorted(p for p in ROOT.rglob("*.py") if "__pycache__" not in p.parts and _module(p) not in RULEBOOK)


def _module(path: Path) -> str:
    return ".".join(path.relative_to(ROOT.parent).with_suffix("").parts)


def _absolute_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = _module(path).split(".")[:-1]
                root = parts[: len(parts) - (node.level - 1)]
                out.append(".".join([*root, node.module] if node.module else root))
            elif node.module:
                out.append(node.module)
    return out


def check_imports() -> list[Finding]:
    findings: list[Finding] = []
    for path in _sources():
        name = _module(path)
        for module in _absolute_imports(path):
            if "agents" in name.split(".") and module.startswith("agentv3.agents.") and module != name:
                findings.append(Finding("imports", "P0", f"{name} imports {module}", f"pytest tests/agentv3/test_boundary.py"))
            if "memory" in name.split(".") and module.startswith(("agentv3.agents", "agentv3.api", "agentv3.orchestrator")):
                findings.append(Finding("imports", "P0", f"{name} imports {module}", f"pytest tests/agentv3/test_boundary.py"))
            if "api" in name.split(".") or "judge" in name.split("."):
                if module.startswith("agentv3.agents"):
                    findings.append(Finding("imports", "P1", f"{name} imports {module}"))
    return findings


def check_contract_limits() -> list[Finding]:
    text = (ROOT / "contracts.py").read_text(encoding="utf-8")
    required = ("MAX_ALTERNATES = 6", "FRAME_MAX_CHARS", "EVIDENCE_MAX_HITS", "DIGEST_MAX_CHARS = 600")
    return [Finding("contracts", "P0", f"missing {item}", "pytest tests/agentv3/test_contracts.py") for item in required if item not in text]


def check_forbidden_phrases() -> list[Finding]:
    findings: list[Finding] = []
    for path in _sources():
        body = path.read_text(encoding="utf-8")
        for phrase in FORBIDDEN_PHRASES:
            if phrase in body:
                findings.append(Finding("forbidden_phrase", "P0", f"{_module(path)} contains {phrase}"))
    return findings


def check_placeholders() -> list[Finding]:
    findings: list[Finding] = []
    for path in _sources():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("raise NotImplementedError") or "TODO" in line or "FIXME" in line:
                findings.append(Finding("placeholder", "P1", f"{_module(path)}:{lineno}", f"sed -n '{lineno}p' {path}"))
    return findings


def check_agent_tests_exist() -> list[Finding]:
    tests = REPO / "agent" / "tests" / "agentv3"
    findings: list[Finding] = []
    for path in sorted((ROOT / "agents").glob("*.py")):
        if path.name == "__init__.py":
            continue
        expected = tests / f"test_{path.stem}.py"
        if not expected.exists():
            findings.append(Finding("coverage_map", "P1", f"no test file for {path.stem}", f"ls {expected}"))
    return findings


def check_prompt_bundle() -> list[Finding]:
    try:
        from ..prompts.bundle import SEGMENT_ORDER, audit_prompt, build_bundle
    except Exception as exc:  # noqa: BLE001 - environment problem, not a pass
        return [Finding("prompt_bundle", "P2", f"cannot import bundle: {type(exc).__name__}")]
    bundle = build_bundle()
    text = bundle.render()
    findings = [Finding("prompt_bundle", "P0", f"audit issue: {issue}") for issue in audit_prompt(text)]
    if SEGMENT_ORDER[0] != "platform_guard":
        findings.append(Finding("prompt_bundle", "P0", "platform_guard is not the first segment"))
    if "crisis_protocol" not in SEGMENT_ORDER[:2]:
        findings.append(Finding("prompt_bundle", "P0", "crisis_protocol is not protected"))
    return findings


def check_lingqu_catalog() -> list[Finding]:
    try:
        from ..lingqu.catalog import load_catalog
        from ..lingqu.contracts import ProviderId
    except Exception as exc:  # noqa: BLE001
        return [Finding("lingqu_catalog", "P2", f"cannot import catalog: {type(exc).__name__}")]
    catalog = load_catalog()
    findings: list[Finding] = []
    if catalog.default_profile != "deepseek/deepseek-v4-flash":
        findings.append(Finding("lingqu_catalog", "P0", f"default is {catalog.default_profile}, expected DeepSeek"))
    if {p.provider for p in catalog.profiles()} != set(ProviderId):
        findings.append(Finding("lingqu_catalog", "P0", "catalog does not cover all five providers"))
    return findings


def check_required_artifacts() -> list[Finding]:
    required = {
        "NOTICE": REPO / "NOTICE",
        "upstream ledger": REPO / "agent" / "docs" / "upstream" / "linger.json",
        "vendor lock": REPO / "agent" / "vendor" / "linger" / "LOCK.json",
        "reuse inventory": REPO / "docs" / "agent-v3" / "reuse-inventory.md",
        "45 coverage": REPO / "docs" / "agent-v3" / "45需求覆盖矩阵.md",
        "locomo fixture": REPO / "agent" / "tests" / "agentv3" / "fixtures" / "locomo10.json",
        "parity test": REPO / "agent" / "tests" / "agentv3" / "test_linger_parity.py",
        "negative probes": REPO / "agent" / "tests" / "agentv3" / "test_negative_probes.py",
        "nightly job": ROOT / "jobs" / "nightly.py",
        "model gateway doc": REPO / "docs" / "agent-v3" / "model-gateway.md",
        "prompt/card doc": REPO / "docs" / "agent-v3" / "prompt-and-cards.md",
        "prompt manifest": ROOT / "prompts" / "manifest.json",
        "prompt golden": ROOT / "prompts" / "golden" / "prompt_bundle.json",
        "example card 1": ROOT / "prompts" / "example_cards" / "gentle-friend.json",
        "example card 2": ROOT / "prompts" / "example_cards" / "mentor.json",
        "example card 3": ROOT / "prompts" / "example_cards" / "family-elder.json",
        "L0 compose": REPO / "deploy" / "compose.agent-v3.yml",
        "L0 smoke": REPO / "scripts" / "dev" / "agent_v3_smoke.sh",
        "L0 smoke report": REPO / "docs" / "agent-v3" / "l0-smoke.json",
        "live provider tests": REPO / "agent" / "tests" / "agentv3" / "lingqu" / "test_live.py",
        "live provider report": REPO / "docs" / "agent-v3" / "lingqu-live-report.json",
        "20 roleplay cases": REPO / "agent" / "tests" / "agentv3" / "roleplay" / "live_cases.json",
        "20 roleplay runner": REPO / "agent" / "tests" / "agentv3" / "roleplay" / "run_live_cases.py",
        "20 roleplay live report": REPO / "docs" / "agent-v3" / "roleplay-20-live-report.json",
    }
    findings = [
        Finding("required_artifact", "P0", f"missing {label}: {path}", f"ls {path}")
        for label, path in required.items()
        if not path.exists()
    ]
    migrations = list((REPO / "backend" / "migrations" / "versions").glob("*agent_v3_memory*.py"))
    if not migrations:
        findings.append(Finding("required_artifact", "P0", "missing agent-v3 memory migration"))
    routes = (ROOT / "api" / "routes.py").read_text(encoding="utf-8")
    for endpoint in ("/v1/v3/turn", "/v1/v3/digest", "/v1/v3/memory", "/v1/v3/purge", "/v1/v3/ingest", "/v1/v3/echo", "/v1/v3/family", "/v1/v3/models", "/v1/v3/cards", "/v1/chat"):
        if endpoint not in routes:
            findings.append(Finding("required_artifact", "P0", f"missing endpoint {endpoint}"))
    return findings


def check_prompt_assets() -> list[Finding]:
    import hashlib
    import json

    from ..prompts.bundle import SEGMENT_ORDER, build_bundle, joined
    from ..prompts.cards import CardSafetyPolicy, parse_card

    assets = ROOT / "prompts"
    findings: list[Finding] = []
    manifest = assets / "manifest.json"
    golden = assets / "golden" / "prompt_bundle.json"
    cards = sorted((assets / "example_cards").glob("*.json"))
    if not manifest.exists():
        findings.append(Finding("prompt_assets", "P0", "missing manifest.json"))
    else:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        if [row["name"] for row in data.get("segments", [])] != list(SEGMENT_ORDER):
            findings.append(Finding("prompt_assets", "P0", "manifest segment order drifted"))
    if not golden.exists():
        findings.append(Finding("prompt_assets", "P0", "missing golden/prompt_bundle.json"))
    else:
        data = json.loads(golden.read_text(encoding="utf-8"))
        text = joined(build_bundle())
        if data.get("render_sha256") != hashlib.sha256(text.encode("utf-8")).hexdigest():
            findings.append(Finding("prompt_assets", "P0", "prompt golden hash drifted"))
    if len(cards) != 3:
        findings.append(Finding("prompt_assets", "P0", f"expected 3 example cards, found {len(cards)}"))
    for path in cards:
        card = parse_card(path.read_text(encoding="utf-8"))
        issues = CardSafetyPolicy().check(card)
        if issues:
            findings.append(Finding("prompt_assets", "P0", f"{path.name}: {issues}"))
    return findings


def check_live_reports_honest() -> list[Finding]:
    """A live report may say skipped, never pass, when no provider answered."""
    import json

    findings: list[Finding] = []
    path = REPO / "docs" / "agent-v3" / "lingqu-live-report.json"
    if not path.exists():
        return [Finding("live_report", "P0", "missing lingqu-live-report.json")]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return [Finding("live_report", "P0", f"unreadable live report: {type(exc).__name__}")]
    rows = data.get("rows", [])
    if data.get("status") == "pass" and rows and all(row.get("status") == "skipped_no_credential" for row in rows):
        findings.append(Finding("live_report", "P0", "live report claims pass while every provider was skipped"))
    for row in rows:
        if row.get("status") == "ok" and not row.get("latency_ms"):
            findings.append(Finding("live_report", "P0", f"ok without latency: {row.get('provider')}"))
    path = REPO / "docs" / "agent-v3" / "roleplay-20-live-report.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("status") == "pass" and data.get("mode") != "live":
            findings.append(Finding("live_report", "P0", "roleplay report claims live pass in non-live mode"))
    return findings


def run_all() -> list[Finding]:
    return [
        *check_imports(),
        *check_contract_limits(),
        *check_forbidden_phrases(),
        *check_placeholders(),
        *check_agent_tests_exist(),
        *check_required_artifacts(),
        *check_prompt_bundle(),
        *check_prompt_assets(),
        *check_lingqu_catalog(),
        *check_live_reports_honest(),
    ]
