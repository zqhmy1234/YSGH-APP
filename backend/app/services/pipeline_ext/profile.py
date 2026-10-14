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
        # 2026-10-14（台账 §7.23 · 批次 8 续审）：**跳过必须留痕**（原为静默 return）。
        # 本钩子在内容主提交后被 pipeline 调用 ⇒ 没文本（照片 caption 未产出 / 语音转写为空）
        # 时画像标注静默不发生，而管线状态仍显示成功 ⇒ "这条记忆为什么没进画像"无从判断。
        # 口径同 §7.20/§7.12：降级要可观测，不静默。
        logger.info("[画像标注] 跳过：内容无文本 content=%s", getattr(content, "id", None))
        return
    from app.services.profile_annotator import annotate_content

    try:
        annotate_content(db, content)
    except Exception as exc:  # noqa: BLE001 —— 标注失败绝不影响内容入库
        logger.warning("B1 画像标注失败 content=%s: %s", content.id, exc)
