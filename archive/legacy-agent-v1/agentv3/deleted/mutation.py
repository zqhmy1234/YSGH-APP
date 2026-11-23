"""Mutation gate: a test that survives a broken line proves nothing.

Ten hand-picked mutants, each mapped to the test that must kill it. A survivor is
a P0 finding: either the test is decoration or the line is dead code.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
AGENT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class Mutant:
    label: str
    file: str
    old: str
    new: str
    test: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant("correction-precedence", "agentv3/memory/ledger.py", "if self._is_corrected(batch.person_id, proposal.text):", "if False:", "test_ledger.py"),  # noqa: E501
    Mutant("batch-idempotency", "agentv3/memory/ledger.py", "if prior is not None:", "if False:", "test_ledger.py"),
    Mutant("evidence-pruned-first", "agentv3/frame.py", 'PRUNE_ORDER = ("evidence", "narrative")', 'PRUNE_ORDER = ("persona", "evidence")', "test_frame.py"),
    Mutant("degrade-not-empty", "agentv3/agents/librarian.py", "status = ResultStatus.PARTIAL if (originals or summaries) else ResultStatus.EMPTY", "status = ResultStatus.GROUNDED", "test_librarian.py"),
    Mutant("llm-down-zero-calls", "agentv3/plan.py", "if llm_down:", "if False:", "test_plan.py"),
    Mutant("private-claim-needs-citation", "agentv3/agents/provenance.py", "if claim.kind in _EVIDENCE_KINDS and not claim.cites:", "if False:", "test_provenance.py"),
    Mutant("digest-unavailable", "agentv3/memory/projection.py", "if entries is None:", "if False:", "test_projection.py"),
    Mutant("crisis-short-circuit", "agentv3/orchestrator.py", "if guard.action is GuardAction.HANDOFF:", "if False:", "test_orchestrator.py"),
    Mutant("sculptor-refuses-bad-batch", "agentv3/agents/sculptor.py", "if issues:", "if False:", "test_sculptor.py"),
    Mutant("sqlite-write-through", "agentv3/adapters/sqlite_memory.py", "doc = json.dumps(self.dump_state(), ensure_ascii=False)", "doc = json.dumps({}, ensure_ascii=False)", "test_ledger.py"),
    Mutant("default-profile-must-be-deepseek", "../config/lingqu-catalog.json", '"default_profile": "deepseek/deepseek-v4.1-flash"', '"default_profile": "qwen/qwen4-max"', "lingqu/test_catalog.py"),
    Mutant("admin-never-returns-key", "agentv3/lingqu/admin.py", '"fingerprint": fingerprint,', '"fingerprint": fingerprint,\\n        "api_key": credential.api_key,', "lingqu/test_gateway.py"),
    Mutant("bad-request-does-not-fallback", "agentv3/lingqu/gateway.py", "if should_fallback(error) and index < len(route.candidates) - 1:", "if index < len(route.candidates) - 1:", "lingqu/test_gateway.py"),
    Mutant("card-cannot-move-platform-guard", "agentv3/prompts/bundle.py", 'SEGMENT_ORDER = (\n    "platform_guard",\n    "crisis_protocol",', 'SEGMENT_ORDER = (\n    "character_card",\n    "platform_guard",\n    "crisis_protocol",', "test_prompt_bundle.py"),
    Mutant("lorebook-probability-zero-never-injects", "agentv3/prompts/cards.py", "if roll >= entry.probability:", "if False:", "test_lorebook.py"),
    Mutant("prompt-budget-must-be-enforced", "agentv3/prompts/bundle.py", "if total <= budget:", "if True:", "test_prompt_bundle.py"),
    Mutant("knowledge-tags-need-asset-layer", "agentv3/agents/sculptor.py", 'if p.tags.library == "knowledge" and p.layer is not MemoryLayer.ASSET:', "if False:", "test_sculptor_tags.py"),
    Mutant("delete-scope-is-per-person", "agentv3/services/tools.py", "PurgeScope(person_id=person, entry_ids=(entry_id,))", 'PurgeScope(person_id="", entry_ids=(entry_id,))', "test_capture_services.py"),
)


PYTEST = os.environ.get(
    "AGENTV3_PYTEST",
    str(AGENT / ".venv312" / "bin" / "pytest") if (AGENT / ".venv312" / "bin" / "pytest").exists() else "/tmp/ghvenv/bin/pytest",
)


def _run(test: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTEST, f"tests/agentv3/{test}", "-x", "-q"],
        cwd=str(AGENT),
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode, (proc.stdout + proc.stderr)[-400:]


def run_mutant(mutant: Mutant) -> dict[str, object]:
    path = ROOT / mutant.file
    original = path.read_text(encoding="utf-8")
    if mutant.old not in original:
        return {"label": mutant.label, "status": "invalid_mutant", "detail": "anchor not found"}
    try:
        path.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        code, tail = _run(mutant.test)
    finally:
        path.write_text(original, encoding="utf-8")
    killed = code != 0
    return {
        "label": mutant.label,
        "file": mutant.file,
        "test": mutant.test,
        "status": "killed" if killed else "survived",
        "detail": "" if killed else tail,
    }


def run_all() -> list[dict[str, object]]:
    return [run_mutant(m) for m in MUTANTS]
