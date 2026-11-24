"""Mutation gate: a test that survives a broken line proves nothing.

Ten hand-picked mutants, each mapped to the test that must kill it. A survivor is
a P0 finding: either the test is decoration or the line is dead code.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

AGENT = Path(__file__).resolve().parents[2]
ROOT = AGENT / "src"


@dataclass(frozen=True)
class Mutant:
    label: str
    file: str
    old: str
    new: str
    test: str


MUTANTS: tuple[Mutant, ...] = (
    Mutant(
        "correction-precedence",
        "memory/ledger.py",
        "if self._is_corrected(batch.person_id, proposal.text):",
        "if False:",
        "test_ledger.py",
    ),
    Mutant("batch-idempotency", "memory/ledger.py", "if prior is not None:", "if False:", "test_ledger.py"),
    Mutant(
        "history-pruned-first",
        "harness/context.py",
        'PRUNE_ORDER = ("history", "narrative", "evidence", "digest", "persona")',
        'PRUNE_ORDER = ("persona", "narrative", "evidence", "digest", "history")',
        "test_frame.py",
    ),
    Mutant(
        "degrade-not-empty",
        "agents/librarian.py",
        "status = ResultStatus.PARTIAL if (originals or summaries) else ResultStatus.EMPTY",
        "status = ResultStatus.GROUNDED",
        "test_librarian.py",
    ),
    Mutant(
        "llm-down-zero-calls",
        "harness/plan.py",
        "        budget = Budget(llm_calls=0, ms=2000, chars=1200)",
        "        budget = Budget(llm_calls=1, ms=2000, chars=1200)",
        "test_plan.py",
    ),
    Mutant(
        "private-claim-needs-citation",
        "agents/provenance.py",
        "if claim.kind in _EVIDENCE_KINDS and not claim.cites:",
        "if False:",
        "test_provenance.py",
    ),
    Mutant("digest-unavailable", "memory/projection.py", "if entries is None:", "if False:", "test_projection.py"),
    Mutant(
        "crisis-short-circuit",
        "harness/orchestrator.py",
        'if turn_plan.response_shape == "handoff":',
        "if False:",
        "test_orchestrator.py",
    ),
    Mutant("sculptor-refuses-bad-batch", "agents/sculptor.py", "if issues:", "if False:", "test_sculptor.py"),
    Mutant(
        "sqlite-write-through",
        "adapters/sqlite_memory.py",
        "doc = json.dumps(self.dump_state(), ensure_ascii=False)",
        "doc = json.dumps({}, ensure_ascii=False)",
        "test_ledger.py",
    ),
    Mutant(
        "default-profile-must-be-deepseek",
        "../config/lingqu-catalog.json",
        '"default_profile": "deepseek/deepseek-v4.1-flash"',
        '"default_profile": "qwen/qwen4-max"',
        "lingqu/test_catalog.py",
    ),
    Mutant(
        "admin-never-returns-key",
        "adapters/lingqu/admin.py",
        '"fingerprint": fingerprint,',
        '"fingerprint": fingerprint,\\n        "api_key": credential.api_key,',
        "lingqu/test_gateway.py",
    ),
    Mutant(
        "bad-request-does-not-fallback",
        "adapters/lingqu/gateway.py",
        "if should_fallback(error) and index < len(route.candidates) - 1:",
        "if index < len(route.candidates) - 1:",
        "lingqu/test_gateway.py",
    ),
    Mutant(
        "card-cannot-move-platform-guard",
        "prompts/bundle.py",
        'SEGMENT_ORDER = (\n    "platform_guard",\n    "crisis_protocol",',
        'SEGMENT_ORDER = (\n    "character_card",\n    "platform_guard",\n    "crisis_protocol",',
        "test_prompt_bundle.py",
    ),
    Mutant(
        "lorebook-probability-zero-never-injects",
        "prompts/cards.py",
        "if roll >= entry.probability:",
        "if False:",
        "test_lorebook.py",
    ),
    Mutant(
        "prompt-budget-must-be-enforced",
        "prompts/bundle.py",
        "if total <= budget:",
        "if True:",
        "test_prompt_bundle.py",
    ),
    Mutant(
        "knowledge-tags-need-asset-layer",
        "agents/sculptor.py",
        'if p.tags.library == "knowledge" and p.layer is not MemoryLayer.ASSET:',
        "if False:",
        "test_sculptor_tags.py",
    ),
    Mutant(
        "delete-scope-is-per-person",
        "tools/runtime.py",
        "PurgeScope(person_id=person, entry_ids=(entry_id,))",
        'PurgeScope(person_id="", entry_ids=(entry_id,))',
        "test_capture_services.py",
    ),
)


PYTEST = os.environ.get(
    "AGENTV3_PYTEST",
    str(AGENT / ".venv312" / "bin" / "pytest")
    if (AGENT / ".venv312" / "bin" / "pytest").exists()
    else "/tmp/ghvenv/bin/pytest",
)


def _clear_pycache() -> None:
    """Mutation edits happen within the same second and may keep file size, so a
    stale .pyc can survive CPython's (mtime,size) check. Clear caches around each
    mutation; otherwise a killed test may be judged from the mutant's bytecode.
    """
    for path in ROOT.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)


def _run(test: str) -> tuple[int, str]:
    proc = subprocess.run(
        [PYTEST, f"tests/agentv3/{test}", "-x", "-q"],
        cwd=str(AGENT),
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
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
        _clear_pycache()
        path.write_text(original.replace(mutant.old, mutant.new, 1), encoding="utf-8")
        code, tail = _run(mutant.test)
    finally:
        path.write_text(original, encoding="utf-8")
        _clear_pycache()
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
