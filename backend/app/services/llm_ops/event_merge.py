"""event_merge.py —— B3 域：L2 主题事件 LLM 语义归并裁决

任务归属：Wave 2 Agent D（B3 云侧域）独占本文件。
实现目标（B3-1/2）：候选组（跨天+地点域连续 5km/12hr 或标签一致，≥2 天 ≥10 张）
→ qwen-flash 只看元数据（时间/地点/标签/OCR 摘要）裁决是否同一事件 + 生成标题；
置信度 <0.7 进"待确认"。经 base.chat_text 调用。
"""
from __future__ import annotations


def merge_verdict(candidate: dict) -> dict:
    """TODO(Wave2-AgentD): LLM 归并裁决；当前返回原候选。"""
    return candidate
