"""F7 冷启动访谈服务（B1-7 · 产品部三问）——枚举集对齐版

三问（产品部已设计）：最重要的人 / 人生转折 / 最骄傲的事
→ L0 维度激活（与《画像维度枚举集_l0.json》枚举值对齐）：
  relation_role + relation_core ← 最重要的人（粗值关系角色 + 细值称谓）
  life_event_major              ← 人生转折（中国式人生大事）
  values_priority               ← 最骄傲的事（反推价值观）
  + L1 兴趣稀疏激活（5-10 维：活跃主题/情绪状态/决策偏好，规则 + LLM 候选）

规则关键词兜底（确定性、可测可联调）→ 统一走 profile_annotator.record_hits
（source=interview：用户主动回答置信度高 0.97、跳过节流）；LLM 枚举标注为真实通道
（llm_ops/annotate.py，mock 同构）。未命中枚举的回答不落画像维度（标注是映射不是生成），
进 profile_dimension_pending 扩展队列。
档案确认闭环：回答 → 复述文本 → 对话式修改（B1-6 后续）。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db.models import ProfileDimensionPending, UserProfile
from app.services.llm_ops.annotate import annotate
from app.services.profile_annotator import (
    HISTORY_LIMIT,  # noqa: F401 —— 对外再导出（历史裁剪常量，测试引用）
    display_dimensions,
    get_or_create_profile,
    record_hits,
)
from app.services.profile_schema import get_schema

logger = logging.getLogger("yishu.interview")

# 产品部三问（V3 玩法 B：AI 访谈式冷启动）
QUESTIONS = [
    {"key": "important_person", "question": "你生命中最重要的人是谁？"},
    {"key": "life_turn", "question": "你经历过的最重要的人生转折是什么？"},
    {"key": "proud_thing", "question": "你最骄傲的一件事是什么？"},
]

# 用户主动作答置信度高（B1 §2.3：冷启动回答与常态标注同链路，主动回答置信度更高）
_ANSWER_CONFIDENCE = 0.97

# 规则枚举映射（确定性兜底；标注是映射不是生成）——relation_role 粗值
_RELATION_ROLE_KEYWORDS = {
    "妈妈": "家人", "母亲": "家人", "爸爸": "家人", "父亲": "家人", "父母": "家人", "家人": "家人",
    "孩子": "家人", "女儿": "家人", "儿子": "家人",
    "老婆": "伴侣", "老公": "伴侣", "妻子": "伴侣", "丈夫": "伴侣", "爱人": "伴侣",
    "女友": "伴侣", "男友": "伴侣", "对象": "伴侣",
    "朋友": "亲友", "挚友": "亲友", "闺蜜": "亲友", "兄弟": "亲友", "姐妹": "亲友",
    "同事": "同事", "同学": "同学",
}
# relation_core 细值称谓（种子值/别名命中；未命中但粗值已覆盖时由粗值承载）
_RELATION_FINE_KEYWORDS = {
    "妈妈": "妈妈", "母亲": "妈妈", "爸爸": "爸爸", "父亲": "爸爸",
    "老婆": "伴侣", "老公": "伴侣", "妻子": "伴侣", "丈夫": "伴侣", "爱人": "伴侣",
    "女友": "伴侣", "男友": "伴侣", "对象": "伴侣",
    "孩子": "孩子", "女儿": "女儿", "儿子": "儿子",
    "闺蜜": "闺蜜", "兄弟": "兄弟", "挚友": "挚友", "朋友": "挚友", "发小": "发小",
    "战友": "战友", "同学": "同学", "室友": "室友", "同事": "同事", "领导": "领导", "导师": "导师",
}
# 人生转折 → life_event_major 中国式人生事件（枚举值对齐）
_LIFE_EVENT_KEYWORDS = {
    "高考": "升学与毕业", "考研": "升学与毕业", "毕业": "升学与毕业",
    "留学": "出国留学与移民", "出国": "出国留学与移民",
    "考公": "考公考编上岸", "考编": "考公考编上岸", "上岸": "考公考编上岸",
    "工作": "求职与入职", "求职": "求职与入职", "入职": "求职与入职",
    "创业": "创业经商", "结婚": "订婚与结婚", "离婚": "分手与离婚",
    "生子": "生育与月子", "生娃": "生育与月子", "怀孕": "生育与月子",
    "搬家": "租房与搬家", "买房": "买房与置业", "生病": "疾病手术与陪护", "手术": "疾病手术与陪护",
    "转行": "升职跳槽转行", "跳槽": "升职跳槽转行", "入伍": "入伍与支教援派",
    "退休": "退休与养老",
}
# 最骄傲的事 → values_priority 价值观（枚举值对齐）
_VALUES_KEYWORDS = {
    "考上": "事业", "创业": "事业", "完成": "事业",
    "坚持": "成长", "跑完": "成长", "学习": "成长",
    "比赛": "自我实现", "获奖": "自我实现", "带大": "责任", "照顾": "责任",
    "健康": "健康", "陪伴": "陪伴",
}

# 冷启动兴趣稀疏激活的 L1 类别（活跃主题/情绪状态/决策偏好/行为习惯）
_INTEREST_CATEGORIES = ("活跃主题与兴趣", "情绪与状态", "决策与偏好", "行为习惯")

_DIM_BY_QUESTION = {
    "important_person": "relation_role",
    "life_turn": "life_event_major",
    "proud_thing": "values_priority",
}


def _dedup_hits(hits: list[dict]) -> list[dict]:
    """按 (dimension, enum_value) 去重（一条回答多关键词可能命中同一值）"""
    seen: set[tuple[str, str]] = set()
    out: list[dict] = []
    for h in hits:
        key = (h["dimension"], h["enum_value"])
        if key in seen:
            continue
        seen.add(key)
        out.append(h)
    return out


def _extract_hits(answers: dict[str, str]) -> list[dict]:
    """三问答案 → L0 枚举对齐命中（规则兜底，确定性；LLM 通道由 annotate 提供）

    relation_role（粗值关系角色）/ relation_core（细值称谓）双写；
    未命中枚举的回答不落画像（闭集约束），由 _queue_unmapped 进扩展队列。
    """
    hits: list[dict] = []
    person = answers.get("important_person", "") or ""
    for k, v in _RELATION_ROLE_KEYWORDS.items():
        if k in person:
            hits.append({"dimension": "relation_role", "enum_value": v, "confidence": _ANSWER_CONFIDENCE})
    for k, v in _RELATION_FINE_KEYWORDS.items():
        if k in person:
            hits.append({"dimension": "relation_core", "enum_value": v, "confidence": _ANSWER_CONFIDENCE})
    turn = answers.get("life_turn", "") or ""
    for k, v in _LIFE_EVENT_KEYWORDS.items():
        if k in turn:
            hits.append({"dimension": "life_event_major", "enum_value": v, "confidence": _ANSWER_CONFIDENCE})
    proud = answers.get("proud_thing", "") or ""
    for k, v in _VALUES_KEYWORDS.items():
        if k in proud:
            hits.append({"dimension": "values_priority", "enum_value": v, "confidence": _ANSWER_CONFIDENCE})
    return _dedup_hits(hits)


def _activate_l1_interests(db: Session, user_id: str, answers: dict[str, str]) -> list[dict]:
    """三问后补 L1 兴趣稀疏激活（5-10 维，规则+LLM 候选）

    组合三问文本 → llm_ops.annotate（dimension_hint=兴趣/情绪/决策类维度）；
    真实通道 qwen-flash 枚举映射，mock 通道种子值+别名匹配（同构）。
    用户主动作答 → 置信度统一提到 0.97。
    """
    combined = "，".join(v for v in answers.values() if v)
    if not combined:
        return []
    schema = get_schema()
    interest_dims = [d.id for d in schema.dims_in_categories(_INTEREST_CATEGORIES)]
    hits = annotate(combined, dimension_hint=interest_dims, confidence=_ANSWER_CONFIDENCE)
    out = []
    for h in hits:
        if h["dimension"] not in interest_dims:
            continue
        out.append({**h, "confidence": _ANSWER_CONFIDENCE})
    return _dedup_hits(out)


def _queue_unmapped(db: Session, user_id: str, answers: dict[str, str], hits: list[dict]) -> None:
    """未命中枚举的回答进维度扩展队列（B1 §2.3：不自动加，累计人工确认）

    同 user+dimension+raw_answer 合并（count+1），status 保持 pending。
    """
    hit_dims = {h["dimension"] for h in hits}
    for q_key, dim in _DIM_BY_QUESTION.items():
        raw = (answers.get(q_key) or "").strip()
        if not raw or dim in hit_dims:
            continue  # 有映射值或空回答 → 不进队列
        stmt = pg_insert(ProfileDimensionPending).values(
            user_id=user_id, dimension=dim, raw_answer=raw
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_pdp_user_dim_raw",
            set_={"count": ProfileDimensionPending.count + 1, "updated_at": datetime.now(timezone.utc)},
        )
        db.execute(stmt)


def _build_confirmation(display: dict[str, list[str]]) -> str:
    """档案确认闭环：复述文本（对用户最重要的是…经历过…最骄傲的是…）"""
    return (
        "我理解到：对你最重要的是{persons}；你经历过{turns}这样的人生转折；"
        "你最骄傲的是{proud}。这些了解对吗？"
    ).format(
        persons="、".join(display.get("relation_role", [])) or "（待补充）",
        turns="、".join(display.get("life_event_major", [])) or "（待补充）",
        proud="、".join(display.get("values_priority", [])) or "（待补充）",
    )


def submit_answers(db: Session, user_id: str, answers: dict[str, str]) -> dict:
    """提交三问答案 → L0 激活 + L1 兴趣稀疏 → 画像更新 + 复述文本

    返回 {dimensions: {dim: [当前值]}, confirmation}（dimensions 为展示格式）。
    """
    hits = _extract_hits(answers)
    hits += _activate_l1_interests(db, user_id, answers)

    # 未命中枚举 → 扩展队列（不落画像维度）；与画像写入同一事务
    _queue_unmapped(db, user_id, answers, hits)

    evidence_text = json.dumps(answers, ensure_ascii=False)
    record_hits(db, user_id, hits, evidence_text=evidence_text, source="interview")

    profile = get_or_create_profile(db, user_id)
    display = display_dimensions(profile.dimensions or {})

    return {"dimensions": display, "confirmation": _build_confirmation(display)}


def get_profile(db: Session, user_id: str) -> dict:
    """查询画像（冷启动激活状态；dimensions 为展示格式 {dim: [当前值]}）"""
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    dimensions = display_dimensions(profile.dimensions or {}) if profile else {}
    return {
        "dimensions": dimensions,
        "version": profile.version if profile else 0,
        "cold_start_done": bool(dimensions),
    }
