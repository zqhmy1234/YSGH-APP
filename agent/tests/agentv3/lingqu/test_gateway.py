from __future__ import annotations

import pytest

from adapters.lingqu.admin import list_models
from adapters.lingqu.catalog import load_catalog
from adapters.lingqu.contracts import ModelReply
from adapters.lingqu.engine import FakeEngine
from adapters.lingqu.errors import BadRequestError, RateLimitError
from adapters.lingqu.gateway import LingquGateway
from harness.contracts import LLMRequest

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
    assert gateway.active().id == "deepseek/deepseek-v4.1-flash"
    assert gateway.telemetry.snapshot()[0]["profile_id"] == "deepseek/deepseek-v4.1-flash"
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
    assert engine.calls == ["deepseek/deepseek-v4.1-flash", "qwen/qwen4-max"]
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
    assert engine.calls == ["deepseek/deepseek-v4.1-flash"]


def test_missing_company_key_skips_to_provider_key():
    gateway = _gateway(env={"COMPANY_LLM_API_KEY": "", "LINGQU_QWEN_API_KEY": "sk-qwen-0001"})
    reply = gateway.complete(LLMRequest(system="s", user="u", max_chars=100))
    assert reply.text == "我在听。"
    calls = gateway.telemetry.snapshot()
    assert calls[0]["status"] == "missing_credential"
    assert calls[1]["profile_id"] == "qwen/qwen4-max"


def test_probe_uses_token_floor_and_health():
    engine = FakeEngine()
    gateway = _gateway(engine)
    health = gateway.probe("deepseek/deepseek-v4.1-flash")
    assert health.state.value == "ok"
    assert health.profile_id == "deepseek/deepseek-v4.1-flash"
    assert engine.calls[0][1].max_tokens == 256


def test_muse_budget_is_floored_for_reasoning_models():
    engine = FakeEngine()
    gateway = _gateway(engine)
    gateway.complete(LLMRequest(system="s", user="u", max_chars=100))
    assert engine.calls[0][1].max_tokens == 256


def test_list_models_never_serializes_key():
    gateway = _gateway()
    data = list_models(gateway)
    text = str(data)
    assert "company-key-placeholder-0001" not in text
    assert data["active_profile"] == "deepseek/deepseek-v4.1-flash"
    assert len(data["profiles"]) == 13
    active = next(p for p in data["profiles"] if p["is_active"])
    assert active["id"] == "deepseek/deepseek-v4.1-flash"
    assert active["credential"] == "company_llm"


def test_engine_uses_openai_prefix_for_company_gateway(monkeypatch):
    from adapters.lingqu.engine import LiteLLMEngine

    captured = {}

    class FakeLiteLLM:
        @staticmethod
        def completion(**kwargs):
            captured.update(kwargs)

            class Message:
                content = "OK"
                tool_calls = None

            class Choice:
                message = Message()
                finish_reason = "stop"

            class Response:
                choices = [Choice()]
                usage = None
                model = "gateway"

            return Response()

    import litellm  # noqa: PLC0415

    monkeypatch.setattr(litellm, "completion", FakeLiteLLM.completion)
    profile = load_catalog(CATALOG).default()
    resolver = __import__("adapters.lingqu.credentials", fromlist=["SecretResolver"]).SecretResolver(
        {
            "COMPANY_LLM_API_KEY": "gateway-key-placeholder-0005",
            "COMPANY_LLM_BASE_URL": "http://127.0.0.1:4000/v1",
        }
    )
    credential = resolver.resolve(profile)
    engine = LiteLLMEngine(timeout_s=1)
    engine.complete(
        profile,
        __import__("adapters.lingqu.contracts", fromlist=["ModelRequest"]).ModelRequest(system="s", user="u"),
        credential,
    )
    assert captured["model"] == f"openai/{profile.model}"
    assert captured["api_base"] == "http://127.0.0.1:4000/v1"

def test_tool_calls_get_room_for_hidden_reasoning():
    """Reasoning models spend max_tokens on reasoning before emitting content.

    Measured on DeepSeek V4.1: 256 tokens with 5 read tools attached returned an
    empty reply, 400 was truncated (finish=length), 800 was clean. Without a
    tool-aware floor every tool-enabled turn degrades to the fallback script.
    """
    engine = FakeEngine()
    gateway = _gateway(engine=engine)
    gateway.complete(LLMRequest(system="s", user="u", tools=("search_memories",)))
    _, request, _ = engine.calls[-1]
    assert request.max_tokens >= 1024

    gateway.complete(LLMRequest(system="s", user="u"))
    _, request, _ = engine.calls[-1]
    assert request.max_tokens >= 256
