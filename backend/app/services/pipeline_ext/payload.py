"""payload.py —— B2 搜索域钩子：扩展内容入库 Qdrant payload

任务归属：Wave 1 Agent A（B2 搜索域）独占本文件。

实现（audit_B2_rag.md #5/#11）：
- FIX-1 content_type 归一：旧值 "image" → 规范值 "photo"（与 pipeline.py 生产
  写入一致；过滤端在 vector_store._to_filter 做双向兼容，见该文件注释）
- payload 补 place：content.place（photo 由 AMAP 逆地理写入 DB）同步进 Qdrant
  payload → NER place 过滤真正命中，不再只靠空结果回退
- payload 补 tags：extra.ci_tags（腾讯云图像识别标签，list[str]）同步进 payload.tags

已知约束（pipeline.py 冻结只读）：_process_photo 里 _index_content 先于
CI 打标/逆地理执行（pipeline.py:377 vs 393/412），photo 首次入库时 place/ci_tags
尚未落库——本钩子对 text/voice 与 photo 重处理路径生效；photo 首入库的 payload
补全需集成 Agent 在 pipeline.py 逆地理/CI 打标后补一次钩子调用（本函数已就绪，
调用方新增一行即可，见完成消息"集成备注"）。
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.db.models import Content


def extend_payload(content: Content, payload: dict[str, Any]) -> dict[str, Any]:
    """入库前扩展 Qdrant payload（FIX-1 归一 + place/tags 同步）

    幂等、可重复调用；任何字段缺失/类型异常都不抛（pipeline 调用方 try/except
    兜底，本函数自身也尽量保守——同步是增强项，不阻断入库主链路）。
    """
    # 1. FIX-1 content_type 归一：遗留 "image" → 规范值 "photo"
    ct = payload.get("content_type")
    if ct == "image":
        payload["content_type"] = "photo"

    # 2. place 同步（NER place 过滤在生产库生效的前提）
    try:
        if getattr(content, "place", None):
            payload["place"] = content.place
    except Exception:  # noqa: BLE001,S110 —— 字段访问异常不影响入库
        pass

    # 3. tags 同步：extra.ci_tags（list[str]）→ payload.tags（与已有 tags 合并）
    try:
        extra = getattr(content, "extra", None) or {}
        ci_tags = extra.get("ci_tags") if isinstance(extra, dict) else None
        if ci_tags:
            tags = [str(t) for t in ci_tags if str(t).strip()]
            if tags:
                existing = payload.get("tags")
                if isinstance(existing, list):
                    merged = list(existing)
                    for t in tags:
                        if t not in merged:
                            merged.append(t)
                    payload["tags"] = merged
                else:
                    payload["tags"] = tags
    except Exception:  # noqa: BLE001,S110
        pass

    return payload
