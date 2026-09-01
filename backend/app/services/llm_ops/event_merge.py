"""event_merge.py —— B3 域：L2 主题事件 LLM 语义归并裁决

任务归属：Wave 2 Agent D（B3 云侧域）独占本文件。
实现目标（B3-1/2）：候选组（跨天+地点域连续 5km/12hr 或标签一致，≥2 天 ≥10 张）
→ qwen-flash 只看元数据（时间/地点/标签/OCR 摘要）裁决是否同一事件 + 生成标题；
置信度 <0.7 进"待确认"。经 base.chat_text 调用，禁止直接 import dashscope。

Mock 通道（无 DASHSCOPE key / MOCK_EXTERNAL_AI=true）：
  - 确定性规则裁决：有主导标签 → 0.8（转正）；否则 0.6（待确认），带模板标题。
  - 与真实 LLM 输出同构（same 键：verdict/confidence/title/llm），切真实 key 无代码改动。
"""
from __future__ import annotations

import logging

from app.services.llm_ops.base import chat_text, llm_available
from app.services.llm_ops.parsing import extract_json_object

logger = logging.getLogger("yishu.event_merge")

# 归并裁决系统提示：只输出 JSON，只看元数据，不看图（B3-2：LLM 只看代表照片元数据）
_MERGE_SYSTEM = (
    "你是照片事件归并裁决器。判断一组照片是否属于同一个事件（如一次旅行、一段备考期）。\n"
    "你只能看到元数据（时间范围、地点、标签、OCR 摘要），看不到图片本身。\n"
    "输出严格 JSON：{\"verdict\": \"merge\"|\"split\", \"confidence\": 0.0-1.0, \"title\": \"中文标题\"}\n"
    "规则：时间连续且地点/主题一致 → merge（confidence 0.7 以上可转正）；"
    "地点跨度大且主题不相关 → split。标题 4-12 字概括（如\"7月云南之旅\"）。只输出 JSON。"
)


def merge_verdict(candidate: dict) -> dict:
    """L2 候选 LLM 归并裁决入口。

    入参 candidate（pipeline._l2_candidates 输出）：cluster/time_range/place_hint/
    tag_hint/ocr_summary/cover_content_id。
    返回 {**candidate, "verdict", "confidence", "title", "llm": "real"|"mock"}；
    调用方（events._write_upper_candidates）按 confidence ≥0.7 转正、<0.7 待确认。
    任何异常（无 key / LLM 失败 / 解析失败）→ 确定性 mock 裁决，不阻断落库。
    """
    if llm_available():
        try:
            return _llm_verdict(candidate)
        except Exception as exc:  # noqa: BLE001 —— 失败降级 mock（不阻断聚合）
            logger.warning("L2 归并 LLM 调用失败，降级 mock: %s", exc)
    return _mock_verdict(candidate)


def _llm_verdict(candidate: dict) -> dict:
    """真实 qwen-flash 裁决（元数据 prompt → JSON 解析）"""
    user = _metadata_prompt(candidate)
    raw = chat_text(_MERGE_SYSTEM, user).strip()
    data = extract_json_object(raw)
    verdict = str(data.get("verdict", "merge")).lower()
    try:
        confidence = float(data.get("confidence", 0.6))
    except (TypeError, ValueError):
        confidence = 0.6
    confidence = max(0.0, min(1.0, confidence))
    title = str(data.get("title") or "").strip() or _fallback_title(candidate)
    return {
        **candidate,
        "verdict": "merge" if verdict == "merge" else "split",
        "confidence": confidence,
        "title": title,
        "llm": "real",
    }


def _mock_verdict(candidate: dict) -> dict:
    """确定性规则裁决（mock / 无 key / LLM 失败兜底；与真实输出同构）"""
    tag = candidate.get("tag")
    tag_hint = candidate.get("tag_hint") or []
    has_tag = bool(tag) or bool(tag_hint)
    confidence = 0.8 if has_tag else 0.6
    return {
        **candidate,
        "verdict": "merge",
        "confidence": confidence,
        "title": _fallback_title(candidate),
        "llm": "mock",
    }


def _metadata_prompt(candidate: dict) -> str:
    """只看元数据（时间/地点/标签/OCR 摘要），不看图（B3-2）"""
    tr = candidate.get("time_range") or []
    span_days = 0
    if len(tr) >= 2:
        try:
            from datetime import datetime

            a = datetime.fromisoformat(tr[0])
            b = datetime.fromisoformat(tr[1])
            span_days = (b.date() - a.date()).days + 1
        except (ValueError, TypeError):
            span_days = 0
    cluster = candidate.get("cluster") or []
    place = candidate.get("place_hint") or "（无地点/降级）"
    tag = candidate.get("tag")
    tag_hint = candidate.get("tag_hint") or []
    seen: list[str] = []
    for x in [*tag_hint, tag]:
        if x and x not in seen:
            seen.append(x)
    tags = "、".join(seen) or "（无）"
    ocr = candidate.get("ocr_summary") or "（无 OCR）"
    return (
        f"时间范围: {tr[0] if tr else '未知'} ~ {tr[1] if len(tr) > 1 else '未知'}（跨度 {span_days} 天）\n"
        f"照片数: {len(cluster)}\n"
        f"地点: {place}\n"
        f"标签: {tags or '（无）'}\n"
        f"OCR 摘要: {ocr}"
    )


def _fallback_title(candidate: dict) -> str:
    """模板兜底标题（LLM 无标题 / mock）"""
    tag = candidate.get("tag") or (candidate.get("tag_hint") or [None])[0] or "主题"
    cluster = candidate.get("cluster") or []
    return f"{tag} · {len(cluster)} 条"
