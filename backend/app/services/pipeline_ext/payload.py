"""payload.py —— B2 搜索域钩子：扩展内容入库 Qdrant payload

任务归属：Wave 1 Agent A（B2 搜索域）独占本文件。
当前为 no-op 占位；Agent A 在此实现：
- payload 补 place（photo 已由 AMAP 逆地理写入 contents.place，需同步进 payload）
- payload 补 tags / ci_tags（腾讯云图像识别标签）
- 修正 content_type 归一（"photo"/"image" 一致化，配合 FIX-1）
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db.models import Content


def extend_payload(content: Content, payload: dict[str, Any]) -> dict[str, Any]:
    # TODO(Wave1-AgentA): 实现 place/tags 同步 + content_type 归一
    return payload
