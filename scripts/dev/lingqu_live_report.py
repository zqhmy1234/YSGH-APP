"""Probe one profile per provider and write a live report.

Usage:
    COMPANY_LLM_API_KEY=... PYTHONPATH=agent/src agent/.venv312/bin/python \
        scripts/dev/lingqu_live_report.py

No key ⇒ every provider is recorded as ``skipped_no_credential`` and the report
says ``not_run_no_credential``. A 401/400 is recorded as ``auth_failed`` or
``bad_request``; nothing is ever upgraded to ``ok`` without a real reply.
Prompt text and credentials are not written.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "agent" / "src"))

from agentv3.lingqu.contracts import HealthState  # noqa: E402
from agentv3.lingqu.gateway import LingquGateway  # noqa: E402

REPORT = ROOT / "docs" / "agent-v3" / "lingqu-live-report.json"
PROVIDERS = ("deepseek", "qwen", "kimi", "minimax", "glm")


def _one_per_provider(gateway: LingquGateway) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for provider in PROVIDERS:
        profiles = [p for p in gateway.profiles() if p.provider.value == provider]
        if not profiles:
            rows.append({"provider": provider, "status": "no_profile"})
            continue
        profile = profiles[0]
        health = gateway.probe(profile.id)
        detail = health.detail.lower()
        if health.state is HealthState.OK:
            status = "ok"
        elif "credential" in detail:
            status = "skipped_no_credential"
        elif "401" in detail or "403" in detail or "auth" in detail:
            status = "auth_failed"
        elif "404" in detail or "invalid model" in detail or "model name" in detail:
            status = "model_or_endpoint_error"
        else:
            status = health.state.value
        rows.append(
            {
                "provider": provider,
                "profile": profile.id,
                "model": profile.model,
                "status": status,
                "state": health.state.value,
                "latency_ms": health.latency_ms,
                "detail": (health.detail or "")[:120],
            }
        )
    return rows


def main() -> int:
    gateway = LingquGateway()
    rows = _one_per_provider(gateway)
    ok = sum(1 for row in rows if row["status"] == "ok")
    skipped = sum(1 for row in rows if row["status"] == "skipped_no_credential")
    status = "pass" if ok == len(PROVIDERS) else "partial"
    if skipped == len(PROVIDERS):
        status = "not_run_no_credential"
    report = {
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "catalog_version": gateway.catalog.catalog_version,
        "default_profile": gateway.catalog.default_profile,
        "providers_total": len(PROVIDERS),
        "ok": ok,
        "skipped": skipped,
        "rows": rows,
        "status": status,
        "note": "只有真实回复才记 ok；缺 Key 记 skipped，鉴权失败记 auth_failed。不含 prompt/key。",
    }
    REPORT.parent.mkdir(parents=True, exist_ok=True)
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    summary = {"status": status, "ok": ok, "skipped": skipped, "report": str(REPORT)}
    print(json.dumps(summary, ensure_ascii=False))
    return 0 if report["status"] == "pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
