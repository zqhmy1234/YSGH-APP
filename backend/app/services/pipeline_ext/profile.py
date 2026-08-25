"""profile.py —— B1 画像域钩子：入库内容画像标注

任务归属：Wave 3 Agent I（B1 画像域）独占本文件。
当前为 no-op 占位；Agent I 在此实现：
- LLM 枚举映射标注（qwen-flash，经 llm_ops/annotate.py，"标注是映射不是生成"）
- 置信度双门槛（普通≥0.7 / 超细性格≥0.8）→ <0.7 进 profile_annotation_pool（Wave 0 已建表）
- 更新规则：同值强度累加 / 异值替换+旧值进 history / 同日同维度节流
- 开放枚举：同义归一 → 别名映射 → 直接新增 value（带证据+时间戳）
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

    from app.db.models import Content


def annotate_on_ingest(db: Session, content: Content) -> None:
    # TODO(Wave3-AgentI): LLM 枚举标注 + 低置信度池 + 更新规则
    return None
