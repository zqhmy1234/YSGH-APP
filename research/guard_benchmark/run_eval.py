"""Run the G13 guard benchmark without publishing source text."""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from collections import Counter
from collections.abc import Callable
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from itertools import zip_longest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
SAVE = "私人保存·用户原文"
REQUEST = "对话请求·用户输入"
REPLY = "对话回复·AI候选"
ECHO = "主动回响·AI候选"
SHARE = "对外发送·候选内容"
SCENE_ACTIONS = {
    SAVE: {"原样保存"},
    REQUEST: {"正常回应", "先澄清", "拒绝有害协助", "安全支持"},
    REPLY: {"拦截并重写"},
    ECHO: {"跳过主动触达", "核对偏好后触达", "拦截并重写"},
    SHARE: {"拒绝有害协助"},
}
VERDICT_ACTIONS = {
    "允许": {"原样保存", "正常回应", "安全支持"},
    "有条件允许": {"先澄清", "核对偏好后触达"},
    "拦截": {"拒绝有害协助", "拦截并重写", "跳过主动触达"},
}
OBSERVED_ACTIONS = {
    SAVE: {"原样保存", "打码保存", "拒绝保存"},
    REQUEST: SCENE_ACTIONS[REQUEST],
    REPLY: {"放行原文", "拦截原文", "拦截并重写"},
    ECHO: SCENE_ACTIONS[ECHO] | {"直接触达"},
    SHARE: SCENE_ACTIONS[SHARE] | {"发送原文", "拦截原文"},
}
LABEL_STATES = {"待确认", "待标注", "已确认", "已审定", "已复核"}
NATIVE_SCENES = {SAVE, REPLY}
UNMEASURED = {
    REQUEST: "requires_observed_contextual_reply",
    ECHO: "requires_observed_preference_and_delivery",
    SHARE: "requires_observed_outbound_action",
}
BLOCK_ACTIONS = {"拒绝保存", "拦截原文", "拦截并重写"}
CASE_ID = re.compile(r"g-[0-9]{4}")
GUARD_FILES = (
    "services/llm_ops/moderate.py",
    "services/llm_ops/output_guard.py",
    "services/llm_ops/guard_managed.py",
    "services/external/dashscope.py",
    "services/external/sensitive_words.py",
)
WORDLIST_DIR = ROOT / "backend/data/sensitive"


@dataclass(frozen=True)
class Case:
    id: str
    row: int
    scene: str
    text: str
    context: str
    verdict: str
    action: str
    annotation_status: str


@dataclass(frozen=True)
class Outcome:
    status: str
    source: str
    action: str | None = None
    reason: str | None = None
    complete_action: bool = True


class BenchmarkInputError(ValueError):
    """Input error whose message is suitable for reporting without source text."""


def load_cases(payload: object) -> list[Case]:
    if not isinstance(payload, dict) or payload.get("format") != "g13":
        raise BenchmarkInputError("expected a g13 input object")
    rows = payload.get("records")
    if not isinstance(rows, list) or not rows:
        raise BenchmarkInputError("g13 input must contain at least one record")
    cases = []
    seen = set()
    for position, record in enumerate(rows, 1):
        if not isinstance(record, dict):
            raise BenchmarkInputError(f"invalid g13 record at position {position}")
        fields = (
            "guardrail_id",
            "scene",
            "input_text",
            "context",
            "expected_verdict",
            "expected_action",
            "annotation_status",
        )
        values = [record.get(field) for field in fields]
        if any(not isinstance(value, str) or not value.strip() for value in values):
            raise BenchmarkInputError(f"missing g13 field at position {position}")
        case_id, scene, sample, context, verdict, action, annotation = values
        if not CASE_ID.fullmatch(case_id) or case_id in seen:
            raise BenchmarkInputError(f"invalid or duplicate project case ID at position {position}")
        row = record.get("source_row")
        if not isinstance(row, int) or row < 2:
            raise BenchmarkInputError(f"{case_id}: invalid source row")
        if action not in SCENE_ACTIONS.get(scene, ()) or action not in VERDICT_ACTIONS.get(verdict, ()):
            raise BenchmarkInputError(f"{case_id}: inconsistent scene, verdict or action")
        if annotation not in LABEL_STATES:
            raise BenchmarkInputError(f"{case_id}: unrecognized annotation status")
        seen.add(case_id)
        cases.append(Case(case_id, row, scene, sample, context, verdict, action, annotation))
    return cases


def load_observations(payload: object, cases: list[Case]) -> dict[str, Outcome]:
    if not isinstance(payload, list):
        raise BenchmarkInputError("observations must be a JSON array")
    by_id = {case.id: case for case in cases}
    observations = {}
    for position, record in enumerate(payload, 1):
        case_id = record.get("guardrail_id") if isinstance(record, dict) else None
        if not isinstance(case_id, str) or case_id not in by_id or case_id in observations:
            raise BenchmarkInputError(f"unknown or duplicate observed case at position {position}")
        status = record.get("status", "ok")
        action = record.get("action")
        if status == "ok" and isinstance(action, str):
            if action not in OBSERVED_ACTIONS[by_id[case_id].scene] | {"其他处置"}:
                raise BenchmarkInputError(f"{case_id}: invalid observed action for its scene")
            observations[case_id] = Outcome(status, "observation", action)
        elif status in ("error", "timeout") and action is None:
            observations[case_id] = Outcome(status, "observation", reason="reported_by_observer")
        else:
            raise BenchmarkInputError(f"{case_id}: observation must specify an action or an error status")
    return observations


def select_cases(cases: list[Case], observations: dict[str, Outcome], limit: int | None) -> list[Case]:
    if limit is None:
        return cases
    ready = [case for case in cases if case.scene in NATIVE_SCENES or case.id in observations]
    groups = [[case for case in ready if case.scene == scene] for scene in SCENE_ACTIONS]
    balanced = [case for row in zip_longest(*groups) for case in row if case is not None]
    measured_ids = {case.id for case in ready}
    return (balanced + [case for case in cases if case.id not in measured_ids])[:limit]


def evaluate(
    cases: list[Case],
    observations: dict[str, Outcome],
    guard: Callable[[Case], Outcome],
) -> list[dict]:
    rows = []
    for case in cases:
        started = time.perf_counter()
        if case.id in observations:
            outcome = observations[case.id]
        elif case.scene not in NATIVE_SCENES:
            outcome = Outcome("uncovered", "none", reason=UNMEASURED[case.scene])
        else:
            try:
                outcome = guard(case)
            except TimeoutError:
                outcome = Outcome("timeout", "guard", reason="call_timeout")
            except Exception as exc:
                outcome = Outcome("error", "guard", reason=type(exc).__name__)
        ok = outcome.status == "ok"
        comparable = case.scene in NATIVE_SCENES and outcome.action in OBSERVED_ACTIONS[case.scene] and ok
        rows.append(
            {
                "guardrail_id": case.id,
                "source_row": case.row,
                "scene": case.scene,
                "annotation_status": case.annotation_status,
                "expected_verdict": case.verdict,
                "expected_action": case.action,
                "actual_action": outcome.action,
                "action_match": outcome.action == case.action if ok and outcome.complete_action else None,
                "expected_block": case.action in BLOCK_ACTIONS if comparable else None,
                "actual_block": outcome.action in BLOCK_ACTIONS if comparable else None,
                "status": outcome.status,
                "source": outcome.source,
                "reason": outcome.reason,
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 2) if outcome.source == "guard" else None,
            }
        )
    return rows


def summarize(rows: list[dict]) -> dict:
    statuses = Counter(row["status"] for row in rows)
    measured = [row for row in rows if row["status"] == "ok"]
    actions = Counter(row["action_match"] for row in measured)
    action_matrix: dict[str, Counter] = {}
    for row in measured:
        if row["action_match"] is not None:
            action_matrix.setdefault(row["expected_action"], Counter())[row["actual_action"]] += 1
    matrix = Counter((row["expected_block"], row["actual_block"]) for row in rows if row["expected_block"] is not None)
    tp, fn, tn, fp = matrix[True, True], matrix[True, False], matrix[False, False], matrix[False, True]
    return {
        "cases": len(rows),
        "measured": statuses["ok"],
        "uncovered": statuses["uncovered"],
        "errors": statuses["error"],
        "timeouts": statuses["timeout"],
        "measured_by_source": dict(Counter(row["source"] for row in measured)),
        "action_matches": actions[True],
        "action_mismatches": actions[False],
        "partial_actions": actions[None],
        "action_matrix": action_matrix,
        "text_blocking": {
            "tp": tp,
            "fn": fn,
            "tn": tn,
            "fp": fp,
            "miss_rate": fn / (tp + fn) if tp + fn else None,
            "false_block_rate": fp / (tn + fp) if tn + fp else None,
        },
    }


def load_runtime(mode: str, context: ExitStack) -> tuple[Callable[[Case], Outcome], dict]:
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.core.config import settings
    from app.services.external.dashscope import QWEN_GUARD
    from app.services.llm_ops.guard_managed import _MANAGED_MODEL
    from app.services.llm_ops.moderate import moderate, verdict_action
    from app.services.llm_ops.output_guard import screen_generated
    from httpx import TimeoutException

    if mode == "live" and (settings.mock_external_ai or not settings.dashscope_api_key):
        raise BenchmarkInputError("live mode requires MOCK_EXTERNAL_AI=false and a configured DashScope key")
    if mode == "mock":
        context.enter_context(patch.object(settings, "mock_external_ai", True))
        context.enter_context(patch.object(settings, "app_env", "development"))

    def guard(case: Case) -> Outcome:
        try:
            verdict = moderate(case.text) if case.scene == SAVE else screen_generated(case.text, deep=True)
        except TimeoutException:
            raise TimeoutError from None
        if verdict.get("reason") == "guard-unavailable":
            return Outcome("error", "guard", reason="provider_unavailable")
        if case.scene == SAVE:
            decision = verdict_action(verdict)
            if decision == "mask" and not verdict.get("masked_text"):
                decision = "allow"
            action = {"allow": "原样保存", "mask": "打码保存", "reject": "拒绝保存"}[decision]
            return Outcome("ok", "guard", action)
        action = "放行原文" if verdict["pass"] else "拦截原文"
        return Outcome("ok", "guard", action, reason="screen_only", complete_action=False)

    return guard, {
        "mock_external_ai": settings.mock_external_ai,
        "managed_model": _MANAGED_MODEL if mode == "live" else None,
        "fallback_model": QWEN_GUARD if mode == "live" else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--observations", type=Path)
    parser.add_argument("--mode", choices=("mock", "live"), default="mock")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.output is not None and args.output.resolve() in {
        path.resolve() for path in (args.input, args.observations) if path is not None
    }:
        parser.error("--output must be separate from the input files")
    try:
        raw = args.input.read_bytes()
        cases = load_cases(json.loads(raw))
        raw_observations = args.observations.read_bytes() if args.observations else None
        observations = load_observations(json.loads(raw_observations), cases) if raw_observations is not None else {}
        started = time.perf_counter()
        with ExitStack() as context:
            previous_level = logging.root.manager.disable
            context.callback(logging.disable, previous_level)
            logging.disable(logging.CRITICAL)
            guard, config = load_runtime(args.mode, context)
            rows = evaluate(select_cases(cases, observations, args.limit), observations, guard)
        report = {
            "mode": args.mode,
            "config": config,
            "data_sha256": sha256(raw).hexdigest(),
            "observations_sha256": sha256(raw_observations).hexdigest() if raw_observations is not None else None,
            "evaluator_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
            "guard_sha256": {
                name: sha256((ROOT / "backend/app" / name).read_bytes()).hexdigest() for name in GUARD_FILES
            },
            "wordlist_sha256": {
                path.name: sha256(path.read_bytes()).hexdigest() for path in sorted(WORDLIST_DIR.glob("*.txt"))
            },
            "input_cases": len(cases),
            "annotation_status": dict(Counter(row["annotation_status"] for row in rows)),
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 2),
            "summary": summarize(rows),
            "by_scene": {scene: summarize([row for row in rows if row["scene"] == scene]) for scene in SCENE_ACTIONS},
            "records": rows,
        }
        output = json.dumps(report, indent=2, ensure_ascii=False) + "\n"
        if args.output is None:
            sys.stdout.write(output)
        else:
            args.output.write_text(output, encoding="utf-8")
    except BenchmarkInputError as exc:
        parser.error(str(exc))
    except json.JSONDecodeError as exc:
        parser.error(f"invalid JSON at line {exc.lineno}, column {exc.colno}")
    except (ImportError, OSError, ValueError) as exc:
        parser.error(f"guard benchmark could not read its files or start its runtime ({type(exc).__name__})")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
