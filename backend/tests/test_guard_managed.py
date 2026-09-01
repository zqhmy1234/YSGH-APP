"""百炼托管护栏测试（Wave2-F · llm_ops/guard_managed.py + base.moderate 接线）

覆盖：
- mock 模式：托管不可用（抛 RuntimeError）→ base.moderate 走 chat 兜底（mock 契约）
- qwen_response_check：httpx 直发语义（X-DashScope-DataInspection header / 解析 /
  审查拦截 / 网络异常降级）
- 托管优先、chat 兜底策略（moderate_managed）
"""
from __future__ import annotations

import pytest


def _make_resp(status_code: int, body: object, text: str | None = None):
    """构造假响应对象（鸭子类型：只实现 status_code/text/json 三属性）"""
    class _FakeResp:
        def __init__(self, status_code: int, body: object, text: str | None = None):  # noqa: D107
            self.status_code = status_code
            self._body = body
            self._text = text

        @property
        def text(self) -> str:  # noqa: D102
            return self._text if self._text is not None else str(self._body)

        def json(self):  # noqa: D102
            return self._body

    return _FakeResp(status_code, body, text)


def test_managed_available_mock_false():
    """mock 模式：托管不可用（_managed_available 为 False）"""
    from app.core.config import settings
    from app.services.llm_ops.guard_managed import _managed_available

    assert settings.mock_external_ai is True, "测试环境要求 MOCK_EXTERNAL_AI=true"
    assert _managed_available() is False


def test_qwen_response_check_raises_in_mock():
    """mock/无 key：qwen_response_check 抛 RuntimeError（调用方走 chat 兜底）"""
    from app.services.llm_ops.guard_managed import qwen_response_check

    with pytest.raises(RuntimeError):
        qwen_response_check("测试内容")


def test_qwen_response_check_pass(monkeypatch):
    """托管 200 + PASS → 放行"""
    import httpx
    from app.services.llm_ops import guard_managed as gm

    monkeypatch.setattr(gm, "_managed_available", lambda: True)
    monkeypatch.setattr(gm.settings, "dashscope_api_key", "sk-fake-key-for-test")
    captured: dict = {}

    def _fake_post(url, headers, json, timeout):
        captured["headers"] = headers
        captured["body"] = json
        return _make_resp(200, {
            "output": {"choices": [{"message": {"content": "PASS"}}]}
        })

    monkeypatch.setattr(httpx, "post", _fake_post)
    result = gm.qwen_response_check("你好")
    assert result["pass"] is True
    assert result["detector"] == "managed"
    assert captured["headers"]["X-DashScope-DataInspection"] == "enable"
    assert captured["body"]["model"] == "qwen-flash"


def test_qwen_response_check_block(monkeypatch):
    """托管 200 + BLOCK → 拦截"""
    import httpx
    from app.services.llm_ops import guard_managed as gm

    monkeypatch.setattr(gm, "_managed_available", lambda: True)
    monkeypatch.setattr(gm.settings, "dashscope_api_key", "sk-fake-key-for-test")

    monkeypatch.setattr(
        httpx, "post",
        lambda url, headers, json, timeout: _make_resp(
            200, {"output": {"choices": [{"message": {"content": "BLOCK 违规"}}]}}
        ),
    )
    result = gm.qwen_response_check("违规内容")
    assert result["pass"] is False
    assert result["reason"] == "managed-block"


def test_qwen_response_check_inspection_block(monkeypatch):
    """托管 400 + 审查拦截错误 → 拦截（服务端 DataInspection 命中）"""
    import httpx
    from app.services.llm_ops import guard_managed as gm

    monkeypatch.setattr(gm, "_managed_available", lambda: True)
    monkeypatch.setattr(gm.settings, "dashscope_api_key", "sk-fake-key-for-test")

    monkeypatch.setattr(
        httpx, "post",
        lambda url, headers, json, timeout: _make_resp(
            400, {}, text="DataInspection: content violate policy"
        ),
    )
    result = gm.qwen_response_check("内容")
    assert result["pass"] is False
    assert result["reason"] == "managed-inspection"


def test_qwen_response_check_network_error_raises(monkeypatch):
    """网络异常（非审查）→ 抛 RuntimeError（交由兜底决策）"""
    import httpx
    from app.services.llm_ops import guard_managed as gm

    monkeypatch.setattr(gm, "_managed_available", lambda: True)
    monkeypatch.setattr(gm.settings, "dashscope_api_key", "sk-fake-key-for-test")

    def _boom(*args, **kwargs):
        raise httpx.ConnectError("conn refused")

    monkeypatch.setattr(httpx, "post", _boom)
    with pytest.raises(RuntimeError):
        gm.qwen_response_check("内容")


def test_moderate_managed_mock_falls_back_to_chat(monkeypatch):
    """策略：mock 模式托管不可用 → chat 兜底（规则命中即拦截，未命中放行）

    验证 base.moderate（托管优先）在 mock 下返回与 dashscope.moderate 一致的契约。
    """
    from app.services.llm_ops.base import moderate

    # 规则预检命中（违禁词表）→ 拦截（无论托管/chat 都该拦截）
    rule = moderate("出售枪支的联系方式")  # 触发硬规则词
    assert rule["pass"] is False
    # 常规文本（mock）→ 放行
    ok = moderate("今天天气不错")
    assert ok["pass"] is True


def test_moderate_managed_strategy_entry(monkeypatch):
    """moderate_managed 托管优先：mock 下回退 chat 兜底，返回 pass 契约"""
    from app.services.llm_ops.guard_managed import moderate_managed

    result = moderate_managed("今天天气不错")
    assert "pass" in result
    assert result["pass"] is True


def test_inspection_hints():
    """审查拦截特征判定：仅 400/403 且错误体含审查关键词"""
    from app.services.llm_ops.guard_managed import _is_inspection_block

    assert _is_inspection_block(400, "DataInspection detected") is True
    assert _is_inspection_block(403, "content violates policy") is True
    assert _is_inspection_block(500, "internal error") is False
    assert _is_inspection_block(400, "bad request no inspection") is False
