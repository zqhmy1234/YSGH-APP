"""LLM 供应商分派（2026-10-15 · 台账 §7.27）。

背景：机器上一把**过期的** `DASHSCOPE_API_KEY`（用户级环境变量）顶掉了 `backend/.env`
里有效的 key，而内容护栏是 **fail-closed** ⇒ 所有内容保存失败（用户看到"含敏感信息
未保存"，App 完全不可用）。故文本调用支持按 `LLM_PROVIDER` 切到 OpenAI 兼容端点。

本文件覆盖：供应商可用判定 / 文本调用分派 / OpenAI 兼容客户端的成败两条路径。
**全部离线**（`httpx.post` 一律打桩，不打死网络）。
"""
import pytest
from app.core.config import settings
from app.services.external import dashscope as ds
from app.services.external import llm_openai


def test_llm_available_is_provider_aware(monkeypatch):
    """可用判定只看**所配供应商**的 key（不得拿另一家的 key 顶着用）。"""
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "llm_provider", "dashscope")
    monkeypatch.setattr(settings, "dashscope_api_key", "dq-key")
    monkeypatch.setattr(settings, "deepseek_api_key", "")
    assert ds._llm_available() is True

    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    assert ds._llm_available() is False, "切 deepseek 后不得靠 dashscope key 判可用"
    monkeypatch.setattr(settings, "deepseek_api_key", "ds-key")
    assert ds._llm_available() is True


def test_mock_disables_both_providers(monkeypatch):
    """`MOCK_EXTERNAL_AI=true` 对两家都关（生产由 config 强制关 mock）。"""
    monkeypatch.setattr(settings, "mock_external_ai", True)
    monkeypatch.setattr(settings, "dashscope_api_key", "dq-key")
    monkeypatch.setattr(settings, "deepseek_api_key", "ds-key")
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    assert ds._llm_available() is False


def test_chat_text_deepseek_routes_to_openai_compat(monkeypatch):
    """切 deepseek ⇒ 走 OpenAI 兼容客户端，且用 DEEPSEEK_* 的 base_url/model。"""
    monkeypatch.setattr(settings, "llm_provider", "deepseek")
    monkeypatch.setattr(settings, "deepseek_api_key", "ds-key")
    monkeypatch.setattr(settings, "deepseek_base_url", "https://api.deepseek.com/v1")
    monkeypatch.setattr(settings, "deepseek_model", "deepseek-chat")
    seen: dict = {}

    def fake(system, user, *, api_key, base_url, model, timeout=30.0):  # noqa: ANN001
        seen.update(
            {"system": system, "user": user, "api_key": api_key, "base_url": base_url, "model": model}
        )
        return "PASS"

    monkeypatch.setattr(llm_openai, "chat_text", fake)
    assert ds._chat_text("sys", "usr") == "PASS"
    assert seen["model"] == "deepseek-chat"
    assert seen["base_url"] == "https://api.deepseek.com/v1"
    assert seen["api_key"] == "ds-key"
    assert seen["system"] == "sys" and seen["user"] == "usr"


def test_openai_compat_client_success_and_failures(monkeypatch):
    """客户端契约：200 且结构正常 → 文本；非 200 / 结构异常 → **抛错**（不返回空串）。

    为什么强调"不返回空串"：空串会被上游当成"模型没判违规" ⇒ 护栏静默放行全部内容
    （正是 fail-closed 要防的方向）。
    """

    class _Resp:
        def __init__(self, code: int, payload: dict | None = None, text: str = "") -> None:
            self.status_code = code
            self._payload = payload or {}
            self.text = text

        def json(self) -> dict:
            return self._payload

    ok = {"choices": [{"message": {"content": "已审核通过"}}]}
    monkeypatch.setattr(llm_openai.httpx, "post", lambda *a, **k: _Resp(200, ok))
    assert (
        llm_openai.chat_text("s", "u", api_key="k", base_url="https://x/v1", model="m")
        == "已审核通过"
    )

    monkeypatch.setattr(llm_openai.httpx, "post", lambda *a, **k: _Resp(401, text="bad key"))
    with pytest.raises(RuntimeError) as ei:
        llm_openai.chat_text("s", "u", api_key="k", base_url="https://x/v1", model="m")
    assert "401" in str(ei.value)

    monkeypatch.setattr(llm_openai.httpx, "post", lambda *a, **k: _Resp(200, {"unexpected": 1}))
    with pytest.raises(RuntimeError):
        llm_openai.chat_text("s", "u", api_key="k", base_url="https://x/v1", model="m")
