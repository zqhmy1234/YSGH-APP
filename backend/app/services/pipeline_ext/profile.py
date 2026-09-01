"""profile.py —— B1 画像域钩子：入库内容画像标注

任务归属：Wave 3 Agent I（B1 画像域）独占本文件。
annotate_on_ingest：文本/语音转写/照片 caption 入库即标注——
LLM 枚举映射（llm_ops/annotate.py，"标注是映射不是生成"）→ 置信度双门槛
（普通≥0.7 / 超细≥0.8）→ <阈值进 profile_annotation_pool →
开放枚举（同义归一→别名表→直接新增 value 带证据+时间戳）→
更新规则（同值强度累加 / 异值替换+旧值进 history / 同日同维度节流）→ 证据锚点 l2_evidence。

fail-safe：任何异常只记日志，绝不阻断内容入库管线（pipeline.py 调用点无异常保护）。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models import Content

logger = logging.getLogger("yishu.pipeline_ext.profile")


def annotate_on_ingest(db: Session, content: Content) -> None:
    if not (content and (content.text or "").strip()):
        return
    from app.services.profile_annotator import annotate_content

    try:
        annotate_content(db, content)
    except Exception as exc:  # noqa: BLE001 —— 标注失败绝不影响内容入库
        logger.warning("B1 画像标注失败 content=%s: %s", content.id, exc)
