"""Judge entry point: static → dynamic → mutation, one JSON verdict.

Fixed output contract (the third party can re-run it):
  exit 0 = all P0 clear; 1 = blocking finding; 2 = environment error (nothing judged).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

from . import checks, mutation, probes
from .evidence import MODULES

AGENT = Path(__file__).resolve().parents[3]
REPORT = AGENT.parent / "docs" / "agent-v3" / "judge-report.json"
JUNIT = Path("/tmp/agentv3-junit.xml")


PYTEST = os.environ.get(
    "AGENTV3_PYTEST",
    str(AGENT / ".venv312" / "bin" / "pytest") if (AGENT / ".venv312" / "bin" / "pytest").exists() else "/tmp/ghvenv/bin/pytest",
)


def run_suite() -> dict[str, object]:
    proc = subprocess.run(
        [PYTEST, "tests/agentv3", "tests/contract", "-q", f"--junitxml={JUNIT}"],
        cwd=str(AGENT),
        env={**os.environ, "PYTHONPATH": "src"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    results = parse_junit()
    return {"exit": proc.returncode, "tail": (proc.stdout + proc.stderr)[-1200:], "tests": results}


def parse_junit() -> dict[str, dict[str, int]]:
    if not JUNIT.exists():
        return {}
    tree = ET.parse(JUNIT)
    out: dict[str, dict[str, int]] = {}
    for case in tree.iter("testcase"):
        cls = case.get("classname", "")
        name = cls.split(".")[-1] if cls else ""
        bucket = out.setdefault(name, {"passed": 0, "failed": 0})
        if any(child.tag in ("failure", "error") for child in case):
            bucket["failed"] += 1
        else:
            bucket["passed"] += 1
    return out


def build_evidence(tests: dict[str, dict[str, int]]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for module, spec in MODULES.items():
        file_stem = Path(spec["tests"]).stem
        counts = tests.get(file_stem, {"passed": 0, "failed": 0})
        verdict = "ok" if counts["passed"] and not counts["failed"] else ("failed" if counts["failed"] else "unverified")
        rows.append({"module": module, **{k: spec[k] for k in ("input", "input_validity", "output", "output_validity")}, "tests": spec["tests"], "passed": counts["passed"], "failed": counts["failed"], "verdict": verdict})
    return rows


def main() -> int:
    if not Path(PYTEST).exists():
        print(f"environment error: pytest missing at {PYTEST}")
        return 2

    findings = checks.run_all()
    suite = run_suite()
    mut_results = mutation.run_all()
    survivors = [m for m in mut_results if m["status"] != "killed"]
    probe_results = probes.run_all()
    bad_probes = [p for p in probe_results if p["status"] != "ok"]

    p0 = [f.as_dict() for f in findings if f.severity == "P0"]
    if suite["exit"] not in (0, 1):
        print("environment error: suite could not run")
        return 2
    blocking = p0 or (suite["exit"] == 1) or survivors or bad_probes
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "pass" if not blocking else "block",
        "static": [f.as_dict() for f in findings],
        "suite": {"exit": suite["exit"], "tail": suite["tail"], "per_file": suite["tests"]},
        "mutation": {"total": len(mutation.MUTANTS), "killed": len(mutation.MUTANTS) - len(survivors), "survivors": survivors, "items": mut_results},
        "probes": {"total": len(probes.PROBES), "ok": len(probes.PROBES) - len(bad_probes), "items": probe_results},
        "evidence": build_evidence(suite["tests"]),
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"static P0={len(p0)} suite_exit={suite['exit']} mutants_killed={report['mutation']['killed']}/{report['mutation']['total']} probes_ok={report['probes']['ok']}/{report['probes']['total']}")
    print(f"verdict={report['verdict']} report={REPORT}")
    return 0 if not blocking else 1


if __name__ == "__main__":
    sys.exit(main())
