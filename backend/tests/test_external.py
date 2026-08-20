"""外部 API 服务层测试（S1-03：百炼接入，mock 模式零费用）

覆盖：
  - 未配置 key / MOCK_EXTERNAL_AI=true → 确定性抛错（调用方走规则兜底）或 mock 放行
  - 护栏 fail-safe 语义：mock 放行；真实模式异常 → 拦截（决策 #12）
"""


import pytest
from app.core.config import settings
from app.services.external import image_caption, moderate, rewrite_query, route_query


@pytest.fixture(autouse=True)
def _ensure_mock_mode():
    """测试必须跑在 mock 模式（不产生费用、不依赖网络）"""
    assert settings.mock_external_ai is True, "测试环境要求 MOCK_EXTERNAL_AI=true"
    yield


def test_rewrite_query_raises_in_mock():
    """mock 模式无真实 key → 抛错，调用方走规则兜底（RAG 改写契约）"""
    with pytest.raises(RuntimeError):
        rewrite_query("去年夏天去的地方")


def test_route_query_raises_in_mock():
    """mock 模式无真实 key → 抛错，调用方走规则兜底（RAG 路由契约）"""
    with pytest.raises(RuntimeError):
        route_query("照片里的猫")


def test_image_caption_raises_in_mock():
    """mock 模式图片塔不可用（需真实 key），调用方应降级"""
    with pytest.raises(RuntimeError):
        image_caption("C:/nonexistent.png")


def test_moderate_mock_passes():
    """护栏 mock 放行（联调不卡流程）"""
    result = moderate("今天天气不错")
    assert result["pass"] is True


def test_moderate_rule_blocks_in_mock():
    """修复（审查 MAJOR）：mock 模式规则预检命中敏感词 → 拦截（不再无条件放行）
    2026-08-20 更新：词表升级开源词库，"转账"为中性词不再拦截，改用"裸聊"等违规词"""
    result = moderate("教你一招：约裸聊加微信")
    assert result["pass"] is False
    assert "敏感词" in result["reason"]


def test_moderate_fail_safe_on_real_failure(monkeypatch):
    """真实模式（配了 key）下百炼不可用 → fail-safe 拦截（决策 #12）"""
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    # 不真正联网：mock _chat_text 抛异常 → 应拦截
    # （2026-08-20 修复：改 qwen-flash 后真实环境 SDK 从 env 读 key 绕过 settings）
    import app.services.external.dashscope as ds_mod

    def boom(system, user, model="qwen-flash"):
        raise RuntimeError("百炼不可用（模拟）")

    monkeypatch.setattr(ds_mod, "_chat_text", boom)
    result = moderate("测试内容")
    assert result["pass"] is False
    assert result["reason"] == "guard-unavailable"
