"""pipeline_ext —— 管线扩展钩子包（Wave 0 建立，2026-08-26）

目的：多 Agent 并行开发时 pipeline.py 冻结为只读（唯一写者 = 集成 Agent），
各功能域通过本包的钩子接入内容入库管线，互不冲突（每域一个模块文件）。

钩子协议（pipeline.py 在关键位置调用，均 try/except 包裹，失败不影响主流程）：
- extend_payload(content, payload) -> dict   内容入库向量库前，扩展 Qdrant payload（B2 place/tags 等）
- mark_sensitive_on_ingest(db, content)      内容入库时打事件级敏感标记（B5b sensitive_tags）
- annotate_on_ingest(db, content)            内容入库时画像标注（B1 LLM 枚举映射）
- consume_emotion(db, content)               语音情绪落库后消费（B5a events.emotion 联动）

各域 Agent 只实现本模块（payload.py / sensitive.py / profile.py / emotion.py），
不得修改 pipeline.py 与 __init__.py 的钩子调用签名。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models import Content

logger = logging.getLogger(__name__)

# 钩子版本：pipeline.py 调用点与本包签名同步，改动需同时 bump
HOOKS_VERSION = 1


def extend_payload(content: Content, payload: dict[str, Any]) -> dict[str, Any]:
    """内容入向量库前扩展 payload（默认 no-op，B2 域 Agent 实现）"""
    try:
        from app.services.pipeline_ext.payload import extend_payload as _impl

        return _impl(content, payload)
    except Exception:  # noqa: BLE001 —— 扩展失败不阻塞入库
        logger.exception("pipeline_ext.extend_payload 失败")
        return payload


def mark_sensitive_on_ingest(db: Session, content: Content) -> None:
    """内容入库时打事件级敏感标记（默认 no-op，B5b 域 Agent 实现）"""
    try:
        from app.services.pipeline_ext.sensitive import mark_sensitive_on_ingest as _impl

        _impl(db, content)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_ext.mark_sensitive_on_ingest 失败")


def annotate_on_ingest(db: Session, content: Content) -> None:
    """内容入库时画像标注（默认 no-op，B1 域 Agent 实现）"""
    try:
        from app.services.pipeline_ext.profile import annotate_on_ingest as _impl

        _impl(db, content)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_ext.annotate_on_ingest 失败")


def consume_emotion(db: Session, content: Content) -> None:
    """语音情绪落库后消费（默认 no-op，B5a 域 Agent 实现）"""
    try:
        from app.services.pipeline_ext.emotion import consume_emotion as _impl

        _impl(db, content)
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_ext.consume_emotion 失败")
