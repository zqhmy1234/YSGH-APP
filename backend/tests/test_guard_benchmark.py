"""Regression tests for the G13 benchmark report and native guard adapters."""

import json
from contextlib import ExitStack
from importlib import import_module

import pytest
from httpx import ReadTimeout

from research.guard_benchmark import run_eval as benchmark

pytestmark = pytest.mark.unit


def _row(number, scene, verdict, action):
    return {
        "guardrail_id": f"g-{number:04d}",
        "source_row": number + 1,
        "scene": scene,
        "input_text": "测试句子，不应出现在报告中。",
        "context": "测试用上下文",
        "expected_verdict": verdict,
        "expected_action": action,
        "annotation_status": "待确认",
    }


def _cases(*rows):
    return benchmark.load_cases({"format": "g13", "records": list(rows)})


def test_input_rejects_empty_duplicate_and_cross_batch_rows():
    row = _row(1, benchmark.SAVE, "允许", "原样保存")
    with pytest.raises(ValueError, match="at least one"):
        _cases()
    with pytest.raises(ValueError, match="duplicate"):
        _cases(row, row)
    with pytest.raises(ValueError, match="project case ID"):
        _cases(row | {"guardrail_id": "sb-zh-00001"})
    with pytest.raises(ValueError, match="inconsistent"):
        _cases(row | {"expected_action": "先澄清"})
    with pytest.raises(ValueError, match="invalid source row"):
        _cases(row | {"source_row": True})


def test_report_separates_partial_observed_uncovered_timeout_and_error():
    cases = _cases(
        _row(1, benchmark.SAVE, "允许", "原样保存"),
        _row(2, benchmark.REPLY, "拦截", "拦截并重写"),
        _row(3, benchmark.REQUEST, "有条件允许", "先澄清"),
        _row(4, benchmark.ECHO, "拦截", "跳过主动触达"),
        _row(5, benchmark.SHARE, "拦截", "拒绝有害协助"),
        _row(6, benchmark.SAVE, "允许", "原样保存"),
    )
    observations = benchmark.load_observations(
        [
            {"guardrail_id": "g-0003", "action": "先澄清"},
            {"guardrail_id": "g-0005", "status": "timeout"},
        ],
        cases,
    )

    def guard(case):
        if case.id == "g-0006":
            raise RuntimeError(case.text)
        if case.scene == benchmark.REPLY:
            return benchmark.Outcome("ok", "guard", "放行原文", complete_action=False)
        return benchmark.Outcome("ok", "guard", "打码保存")

    rows = benchmark.evaluate(cases, observations, guard)
    summary = benchmark.summarize(rows)
    assert summary["measured"] == 3
    assert (summary["uncovered"], summary["timeouts"], summary["errors"]) == (1, 1, 1)
    assert (summary["action_matches"], summary["action_mismatches"], summary["partial_actions"]) == (1, 1, 1)
    assert summary["action_matrix"] == {"原样保存": {"打码保存": 1}, "先澄清": {"先澄清": 1}}
    assert summary["text_blocking"] == {"tp": 0, "fn": 1, "tn": 1, "fp": 0, "miss_rate": 1.0, "false_block_rate": 0.0}
    assert benchmark.summarize([rows[2]])["text_blocking"]["miss_rate"] is None
    assert cases[0].text not in json.dumps(rows, ensure_ascii=False)
    assert [case.id for case in benchmark.select_cases(cases, observations, 3)] == [
        "g-0001",
        "g-0003",
        "g-0002",
    ]
    with pytest.raises(ValueError, match="duplicate"):
        benchmark.load_observations([{"guardrail_id": "g-0003", "action": "先澄清"}] * 2, cases)


def test_runtime_uses_real_guard_entrypoints_under_mock_configuration(monkeypatch):
    from app.core.config import settings
    from app.services.llm_ops import output_guard

    moderation = import_module("app.services.llm_ops.moderate")
    save, reply = _cases(
        _row(1, benchmark.SAVE, "允许", "原样保存"),
        _row(2, benchmark.REPLY, "拦截", "拦截并重写"),
    )
    calls = []

    def fake_moderate(text):
        calls.append(("moderate", text))
        return {"pass": True, "action": "mask", "masked_text": "***"}

    def fake_generated(text, *, deep):
        calls.append(("generated", text, deep))
        return {"pass": False, "reason": "rule-reject"}

    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(moderation, "moderate", fake_moderate)
    monkeypatch.setattr(output_guard, "screen_generated", fake_generated)
    with ExitStack() as context:
        guard, config = benchmark.load_runtime("mock", context)
        assert config["mock_external_ai"] is True
        assert guard(save).action == "打码保存"
        assert guard(reply) == benchmark.Outcome("ok", "guard", "拦截原文", "screen_only", False)
    assert settings.mock_external_ai is False
    assert calls == [("moderate", save.text), ("generated", reply.text, True)]


def test_unavailable_or_timed_out_guard_never_scores_as_a_block(monkeypatch):
    moderation = import_module("app.services.llm_ops.moderate")
    cases = _cases(*[_row(n, benchmark.SAVE, "允许", "原样保存") for n in (1, 2)])
    outcomes = iter([{"pass": False, "reason": "guard-unavailable"}, None])

    def fake_moderate(text):
        verdict = next(outcomes)
        if verdict is None:
            raise ReadTimeout("timeout")
        return verdict

    monkeypatch.setattr(moderation, "moderate", fake_moderate)
    with ExitStack() as context:
        guard, _ = benchmark.load_runtime("mock", context)
        rows = benchmark.evaluate(cases, {}, guard)
    assert [row["status"] for row in rows] == ["error", "timeout"]
    assert all(row["actual_block"] is None and row["action_match"] is None for row in rows)
