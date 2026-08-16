"""外部 API 服务层测试（S1-03：百炼接入，mock 模式零费用）

覆盖：
  - 未配置 key / MOCK_EXTERNAL_AI=true → 确定性抛错（调用方走规则兜底）或 mock 放行
  - 护栏 fail-safe 语义：mock 放行；真实模式异常 → 拦截（决策 #12）
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

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
    assert result["reason"] == "mock"


def test_moderate_fail_safe_on_real_failure(monkeypatch):
    """真实模式（配了 key）下百炼不可用 → fail-safe 拦截（决策 #12）"""
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    # 不真正联网：让 Generation.call 抛网络异常 → 应拦截
    result = moderate("测试内容")
    assert result["pass"] is False
    assert result["reason"] == "guard-unavailable"
