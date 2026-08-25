"""annotate.py —— B1 域：画像枚举标注

任务归属：Wave 3 Agent I（B1 画像域）独占本文件。
实现目标（B1-1）：把自然语言归一为枚举值（"标注是映射不是生成"）——
种子值匹配 → 同义归一（别名表）→ 直接新增 value（带证据+时间戳）；
输出 {dimension, enum_value, confidence}，供 pipeline_ext/profile.py 消费。
经 base.chat_text 调用。
"""
from __future__ import annotations


def annotate(text: str) -> list[dict]:
    """TODO(Wave3-AgentI): LLM 枚举映射标注；当前返回空（不更新）。"""
    return []
