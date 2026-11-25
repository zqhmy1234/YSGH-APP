"""Observability: credentials and phone numbers never reach a log line."""

from harness.obs import CostMeter, Degradation, Tracer, redact


def test_sensitive_keys_are_masked():
    assert redact({"api_key": "sk-1", "nested": {"token": "t"}}) == {"api_key": "***", "nested": {"token": "***"}}


def test_phone_number_is_masked():
    assert redact("电话 13800138000 别外传") == "电话 *** 别外传"


def test_tracer_records_ms_and_fields():
    tracer = Tracer()
    with tracer.span("turn", person="p1"):
        pass
    assert tracer.spans[0]["name"] == "turn" and tracer.spans[0]["person"] == "p1"


def test_degradation_counts_by_reason():
    d = Degradation()
    d.note("llm")
    d.note("llm")
    assert d.snapshot()["total"] == 2 and d.reasons() == ("llm",)


def test_cost_meter_counts_calls_and_chars():
    meter = CostMeter()
    meter.add_llm(10)
    meter.add_llm(5)
    assert meter.llm_calls == 2 and meter.chars == 15
