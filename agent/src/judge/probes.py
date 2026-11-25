"""Eight north-star probes, replayed as red→green evidence.

For each probe: weaken the implementation, the probe must go red; restore, it must
go green. A probe that stays green while weakened is decoration (P0), a probe
that stays red after restore means the restore failed (environment, P2).
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
class Probe:
    label: str
    file: str
    old: str
    new: str
    node: str


PROBES: tuple[Probe, ...] = (
    Probe(
        "probe-1-private-needs-cite",
        "agents/provenance.py",
        "if claim.kind in _EVIDENCE_KINDS and not claim.cites:",
        "if False:",
        "tests/agentv3/test_negative_probes.py::test_probe_1_private_claim_without_citation_is_not_passed",
    ),
    Probe(
        "probe-2-correction-wins",
        "memory/ledger.py",
        "if self._is_corrected(batch.person_id, proposal.text):",
        "if False:",
        "tests/agentv3/test_negative_probes.py::test_probe_2_sculptor_cannot_override_user_correction",
    ),
    Probe(
        "probe-3-boundary-never-pruned",
        "harness/context.py",
        "if stable.corrections:",
        "if False:",
        "tests/agentv3/test_negative_probes.py::test_probe_3_boundary_is_never_pruned",
    ),
    Probe(
        "probe-4-empty-is-not-three-meaning",
        "agents/librarian.py",
        'gaps.append(f"{name}_unavailable")',
        "pass",
        "tests/agentv3/test_negative_probes.py::test_probe_4_empty_does_not_carry_three_meanings",
    ),
    Probe(
        "probe-5-degraded-no-clarify",
        "agents/librarian.py",
        'if status is ResultStatus.EMPTY and query.mode == "evidence" and not channel_down:',
        'if status is ResultStatus.EMPTY and query.mode == "evidence":',
        "tests/agentv3/test_negative_probes.py::test_probe_5_degraded_turn_never_asks_a_clarify",
    ),
    Probe(
        "probe-6-double-run-idempotent",
        "memory/ledger.py",
        "if prior is not None:",
        "if False:",
        "tests/agentv3/test_negative_probes.py::test_probe_6_double_nightly_run_is_idempotent",
    ),
    Probe(
        "probe-7-muse-cannot-write",
        "harness/registry.py",
        "name=AgentName.MUSE,\n        may_write=False,",
        "name=AgentName.MUSE,\n        may_write=True,",
        "tests/agentv3/test_negative_probes.py::test_probe_7_muse_holds_no_write_handle",
    ),
    Probe(
        "probe-9-guard-is-first-segment",
        "prompts/bundle.py",
        'SEGMENT_ORDER = (\n    "platform_guard",\n    "crisis_protocol",',
        'SEGMENT_ORDER = (\n    "character_card",\n    "platform_guard",\n    "crisis_protocol",',
        "tests/agentv3/test_prompt_bundle.py::test_bundle_has_ten_ordered_segments_and_guard_first",
    ),
    Probe(
        "probe-10-knowledge-tags-require-asset",
        "agents/sculptor.py",
        'if p.tags.library == "knowledge" and p.layer is not MemoryLayer.ASSET:',
        "if False:",
        "tests/agentv3/test_sculptor_tags.py::test_knowledge_tags_require_asset_layer",
    ),
    Probe(
        "probe-11-delete-is-scoped",
        "tools/runtime.py",
        "PurgeScope(person_id=person, entry_ids=(entry_id,))",
        'PurgeScope(person_id="", entry_ids=(entry_id,))',
        "tests/agentv3/test_capture_services.py::test_delete_does_not_touch_other_person",
    ),
    Probe(
        "probe-8-originals-before-empty",
        "agents/librarian.py",
        'if "search" not in degraded:',
        "if False:",
        "tests/agentv3/test_negative_probes.py::test_probe_8_never_says_no_record_without_querying_originals",
    ),
)


PYTEST = os.environ.get(
    "AGENTV3_PYTEST",
    str(AGENT / ".venv312" / "bin" / "pytest")
    if (AGENT / ".venv312" / "bin" / "pytest").exists()
    else "/tmp/ghvenv/bin/pytest",
)


def _clear_pycache() -> None:
    """See mutation.py: same-second edits can leave a stale .pyc behind."""
    for path in ROOT.rglob("__pycache__"):
        shutil.rmtree(path, ignore_errors=True)


def _run(node: str) -> int:
    proc = subprocess.run(
        [PYTEST, node, "-x", "-q"],
        cwd=str(AGENT),
        env={**os.environ, "PYTHONPATH": "src", "PYTHONDONTWRITEBYTECODE": "1"},
        capture_output=True,
        text=True,
        timeout=120,
    )
    return proc.returncode


def run_probe(probe: Probe) -> dict[str, object]:
    path = ROOT / probe.file
    original = path.read_text(encoding="utf-8")
    if probe.old not in original:
        return {"label": probe.label, "status": "invalid_probe", "detail": "anchor not found"}
    try:
        _clear_pycache()
        path.write_text(original.replace(probe.old, probe.new, 1), encoding="utf-8")
        red = _run(probe.node) != 0
    finally:
        path.write_text(original, encoding="utf-8")
        _clear_pycache()
    green = _run(probe.node) == 0
    if red and green:
        status = "ok"
    elif not red:
        status = "not_red_when_weakened"
    else:
        status = "not_green_after_restore"
    return {"label": probe.label, "status": status, "node": probe.node}


def run_all() -> list[dict[str, object]]:
    return [run_probe(p) for p in PROBES]
