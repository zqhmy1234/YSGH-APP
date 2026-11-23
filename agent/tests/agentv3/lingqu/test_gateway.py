from __future__ import annotations

import pytest

from agentv3.contracts import LLMRequest
from agentv3.lingqu.admin import list_models
from agentv3.lingqu.catalog import load_catalog
from agentv3.lingqu.contracts import ModelReply
from agentv3.lingqu.engine import FakeEngine
from agentv3.lingqu.errors import BadRequestError, RateLimitError
from agentv3.lingqu.gateway import LingquGateway

from .test_catalog import CATALOG


def _gateway(engine=None, env=None):
    return LingquGateway(
        catalog=load_catalog(CATALOG),
        resolver=None,
        engine=engine or FakeEngine(),
        env={"COMPANY_LLM_API_KEY": "company-key-placeholder-0001", **(env or {})},
    )


def test_default_route_is_deepseek_and_returns_text():
    gateway = _gateway()
    reply = gateway.complete(LLMRequest(system="你是忆光。", user="在吗", max_chars=100))
    assert reply.text == "我在听。"
    assert gateway.active().id == "deepseek/deepseek-v4-flash"
    assert gateway.telemetry.snapshot()[0]["profile_id"] == "deepseek/deepseek-v4-flash"
    assert "system" not in gateway.telemetry.snapshot()[0]
    assert "user" not in gateway.telemetry.snapshot()[0]


def test_rate_limit_falls_back_to_next_profile():
    class OneError:
        def __init__(self):
            self.calls = []

        def complete(self, profile, request, credential):
            self.calls.append(profile.id)
            if len(self.calls) == 1:
                raise RateLimitError("429 too many requests", status_code=429)
            return ModelReply(text="回退成功", profile_id=profile.id, model=profile.model)

    engine = OneError()
    gateway = _gateway(engine)
    reply = gateway.complete(LLMRequest(system="s", user="u", max_chars=100))
    assert reply.text == "回退成功"
    assert engine.calls == ["deepseek/deepseek-v4-flash", "qwen/qwen4-max"]
    assert [row["status"] for row in gateway.telemetry.snapshot()] == ["error", "ok"]


def test_bad_request_does_not_fall_back():
    class Bad:
        def __init__(self):
            self.calls = []

        def complete(self, profile, request, credential):
            self.calls.append(profile.id)
            raise BadRequestError("invalid parameter", status_code=400)

    engine = Bad()
    gateway = _gateway(engine)
    with pytest.raises(Exception) as exc:
        gateway.complete(LLMRequest(system="s", user="u", max_chars=100))
    assert "bad_request" in str(exc.value)
    assert engine.calls == ["deepseek/deepseek-v4-flash"]


def test_missing_company_key_skips_to_provider_key():
    gateway = _gateway(env={"COMPANY_LLM_API_KEY": "", "LINGQU_QWEN_API_KEY": "sk-qwen-0001"})
    reply = gateway.complete(LLMRequest(system="s", user="u", max_chars=100))
    assert reply.text == "我在听。"
    calls = gateway.telemetry.snapshot()
    assert calls[0]["status"] == "missing_credential"
    assert calls[1]["profile_id"] == "qwen/qwen4-max"


def test_probe_uses_64_tokens_and_health():
    gateway = _gateway()
    health = gateway.probe("deepseek/deepseek-v4-flash")
    assert health.state.value == "ok"
    assert health.profile_id == "deepseek/deepseek-v4-flash"


def test_list_models_never_serializes_key():
    gateway = _gateway()
    data = list_models(gateway)
    text = str(data)
    assert "company-key-placeholder-0001" not in text
    assert data["active_profile"] == "deepseek/deepseek-v4-flash"
    assert len(data["profiles"]) == 13
    active = next(p for p in data["profiles"] if p["is_active"])
    assert active["id"] == "deepseek/deepseek-v4-flash"
    assert active["credential"] == "company_llm"
