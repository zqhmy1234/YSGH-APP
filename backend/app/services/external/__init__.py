"""外部 API 服务层（S1-03 接入准备）

- dashscope：阿里云百炼（qwen-flash 文本 / Qwen3-VL 图片塔 / 护栏）
- 统一约定：未配置密钥或 MOCK_EXTERNAL_AI=true 时，调用方走规则/mock 兜底，
  拿到真实 key 后零代码切换（同构响应）。
"""
from app.services.external.dashscope import (
    image_caption,
    moderate,
    rewrite_query,
    route_query,
)

__all__ = ["image_caption", "moderate", "rewrite_query", "route_query"]
