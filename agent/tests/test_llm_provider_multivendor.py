"""W适配（2026-10-08）：llm_provider 双供应商解耦的回归测试

覆盖：
1. 百炼默认路径行为不变（key 前缀 → base_url 配对、workspace header）
2. 通用 OpenAI 兼容路径（AGENT_LLM_API_KEY/BASE_URL）：base_url 直通、
   is_dashscope=False、**workspace header 不发**（残留的 DASHSCOPE_WORKSPACE_ID
   不得泄漏给别的供应商）、模型可独立指定
3. 两个 key 都缺 ⇒ LlmConfigError（宁可启动即失败）
4. build_chat_model：enable_thinking extra_body 仅百炼路径携带
"""
import pytest
from llm_provider import (
    PUBLIC_BASE_URL,
    LlmConfigError,
    build_chat_model,
    load_llm_settings,
    reset_llm_cache,
)


@pytest.fixture(autouse=True)
def _clean_llm_env(monkeypatch):
    """隔离环境：本文件每个用例自己决定全部 LLM 变量（防宿主残留干扰）。

    顺带清 build_chat_model 的 lru_cache：它按 (settings, streaming) 缓存
    ChatOpenAI 实例，用例自造的 settings 会留在缓存里污染后续用例。
    """
    for var in (
        "DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID", "DASHSCOPE_REGION",
        "DASHSCOPE_BASE_URL", "AGENT_LLM_API_KEY", "AGENT_LLM_BASE_URL",
        "AGENT_LLM_MODEL", "AGENT_ENABLE_THINKING",
    ):
        monkeypatch.delenv(var, raising=False)
    reset_llm_cache()
    yield
    reset_llm_cache()


# ── 1. 百炼路径行为不变 ─────────────────────────────────────────────


def test_dashscope_generic_key_keeps_public_endpoint(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dash")
    s = load_llm_settings()
    assert s.base_url == PUBLIC_BASE_URL
    assert s.is_dashscope is True
    assert s.extra_headers == {}


def test_dashscope_ws_key_gets_workspace_header(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-ws-abc123")
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-42")
    s = load_llm_settings()
    assert s.is_dashscope is True
    assert s.extra_headers == {"X-DashScope-WorkSpace": "ws-42"}


# ── 2. 通用 OpenAI 兼容路径 ─────────────────────────────────────────


def test_generic_provider_overrides_dashscope(monkeypatch):
    """AGENT_LLM_* 三件套切换到 DeepSeek；.env 的百炼配置全部让位。"""
    monkeypatch.setenv("AGENT_LLM_API_KEY", "sk-ds")
    monkeypatch.setenv("AGENT_LLM_BASE_URL", "https://api.deepseek.com/v1")
    monkeypatch.setenv("AGENT_LLM_MODEL", "deepseek-chat")
    # 模拟 .env/宿主残留的百炼配置——不得影响通用路径
    monkeypatch.setenv("DASHSCOPE_WORKSPACE_ID", "ws-residual")
    s = load_llm_settings()
    assert s.base_url == "https://api.deepseek.com/v1"
    assert s.model == "deepseek-chat"
    assert s.is_dashscope is False
    # ⚠️ 关键断言：残留 workspace 不得发给别的供应商
    assert s.extra_headers == {}


def test_generic_key_error_message_mentions_both_paths():
    with pytest.raises(LlmConfigError) as ei:
        load_llm_settings()
    assert "AGENT_LLM_API_KEY" in str(ei.value)
    assert "DASHSCOPE_API_KEY" in str(ei.value)


# ── 3. build_chat_model 的 extra_body 分流 ──────────────────────────


def test_chat_model_extra_body_only_for_dashscope(monkeypatch, caplog):
    from llm_provider import LlmSettings

    dash = LlmSettings(
        api_key="sk-x", base_url=PUBLIC_BASE_URL, model="qwen3.8-flash",
        enable_thinking=True, temperature=0.7, max_tokens=2048, timeout=30,
    )
    generic = LlmSettings(
        api_key="sk-x", base_url="https://api.deepseek.com/v1", model="deepseek-chat",
        enable_thinking=True, temperature=0.7, max_tokens=2048, timeout=30,
        is_dashscope=False,
    )
    m1 = build_chat_model(dash)
    m2 = build_chat_model(generic)
    # langchain-openai 的 ChatOpenAI 把 extra_body 存为同名字段
    assert m1.extra_body == {"enable_thinking": True}
    assert not m2.extra_body, "非百炼供应商不得收到 enable_thinking"
