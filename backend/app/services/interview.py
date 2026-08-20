"""F7 冷启动访谈服务（B1-7 · 产品部三问）

三问（产品部已设计）：最重要的人 / 人生转折 / 最骄傲的事
→ 维度激活顺序（B1-7 技术侧）：
  L0 关系核心（relation_core）← 最重要的人
  L0 人生大事时间线（life_events）← 人生转折
  L0 价值观（values_priority）← 最骄傲的事（反推）
  + L1 兴趣稀疏（interests，5-10 维）｜L2 留白（等事件积累，防画像幻觉）

映射：规则关键词兜底（B1"标注是映射不是生成"，MVP 可测可联调）；LLM 枚举映射
（qwen-flash）为后续增强，当前未实现——配 key 后由外部模块接入，见 B1 2.3。
未命中枚举的回答不落画像维度（闭集约束），进 profile_dimension_pending 扩展队列。
档案确认闭环：回答 → 复述文本 → 对话式修改（B1-6 后续）。
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import ProfileDimensionHistory, ProfileDimensionPending, UserProfile

logger = logging.getLogger("yishu.interview")

# 产品部三问（V3 玩法 B：AI 访谈式冷启动）
QUESTIONS = [
    {"key": "important_person", "question": "你生命中最重要的人是谁？"},
    {"key": "life_turn", "question": "你经历过的最重要的人生转折是什么？"},
    {"key": "proud_thing", "question": "你最骄傲的一件事是什么？"},
]

# 规则枚举映射（LLM 兜底，B1 枚举集 MVP 简化版）
_PERSON_KEYWORDS = {
    "妈妈": "家人", "母亲": "家人", "爸爸": "家人", "父亲": "家人", "父母": "家人",
    "家人": "家人", "孩子": "家人", "女儿": "家人", "儿子": "家人",
    "老婆": "伴侣", "老公": "伴侣", "妻子": "伴侣", "丈夫": "伴侣", "爱人": "伴侣",
    "女友": "伴侣", "男友": "伴侣", "对象": "伴侣",
    "朋友": "挚友", "挚友": "挚友", "闺蜜": "挚友", "兄弟": "挚友", "姐妹": "挚友",
}
_TURN_KEYWORDS = ["高考", "考研", "留学", "出国", "毕业", "工作", "创业", "结婚",
                  "离婚", "生子", "生娃", "搬家", "生病", "手术", "转行", "入伍"]
_PRIORITY_KEYWORDS = {
    "考上": "成就", "创业": "成就", "坚持": "坚持", "完成": "成就", "比赛": "成就",
    "获奖": "成就", "跑完": "坚持", "毕业": "成就", "带大": "责任", "照顾": "责任",
}

# 历史值保留最近 10 条（B1 2.3 有界列表）
HISTORY_LIMIT = 10


def _extract_important_person(answer: str) -> list[str]:
    vals = [v for k, v in _PERSON_KEYWORDS.items() if k in answer]
    return list(dict.fromkeys(vals))


def _extract_life_turn(answer: str) -> list[str]:
    vals = [k for k in _TURN_KEYWORDS if k in answer]
    return list(dict.fromkeys(vals))


def _extract_values(answer: str) -> list[str]:
    vals = [v for k, v in _PRIORITY_KEYWORDS.items() if k in answer]
    return list(dict.fromkeys(vals))


def _extract_answers(answers: dict[str, str]) -> dict[str, list[str]]:
    """三问答案 → 维度值（规则兜底；LLM 路径配 key 后由外部模块接管）

    修复（审查 MINOR·B1 闭集）：未命中枚举不再返回"其他"（枚举外值不落画像，
    标注是映射不是生成），交由 _queue_unmapped 进维度扩展队列。
    """
    return {
        "relation_core": _extract_important_person(answers.get("important_person", "")),
        "life_events": _extract_life_turn(answers.get("life_turn", "")),
        "values_priority": _extract_values(answers.get("proud_thing", "")),
    }


_DIM_BY_QUESTION = {
    "important_person": "relation_core",
    "life_turn": "life_events",
    "proud_thing": "values_priority",
}


def _queue_unmapped(db: Session, user_id: str, answers: dict[str, str], dims: dict[str, list[str]]) -> None:
    """未命中枚举的回答进维度扩展队列（B1 2.3：不自动加，累计人工确认）

    同 user+dimension+raw_answer 合并（count+1），status 保持 pending。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    for q_key, dim in _DIM_BY_QUESTION.items():
        raw = (answers.get(q_key) or "").strip()
        if not raw or dims[dim]:
            continue  # 有映射值或空回答 → 不进队列
        stmt = pg_insert(ProfileDimensionPending).values(
            user_id=user_id, dimension=dim, raw_answer=raw
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_pdp_user_dim_raw",
            set_={"count": ProfileDimensionPending.count + 1, "updated_at": datetime.now(timezone.utc)},
        )
        db.execute(stmt)


def _trim_history(db: Session, user_id: str, dims: dict[str, list[str]]) -> None:
    """历史值裁剪：每维度仅保留最近 10 条（B1 2.3 有界列表）

    修复（审查 MINOR）：原注释"保留最近 10 条由查询侧控制"但写入无限增长——
    改为写入侧主动裁剪（查询侧不再背这个锅）。
    """
    for dim in dims:
        keep_ids = db.execute(
            select(ProfileDimensionHistory.id)
            .where(
                ProfileDimensionHistory.user_id == user_id,
                ProfileDimensionHistory.dimension == dim,
            )
            .order_by(ProfileDimensionHistory.updated_at.desc(), ProfileDimensionHistory.id.desc())
            .limit(HISTORY_LIMIT)
        ).scalars().all()
        if keep_ids:
            db.execute(
                delete(ProfileDimensionHistory).where(
                    ProfileDimensionHistory.user_id == user_id,
                    ProfileDimensionHistory.dimension == dim,
                    ProfileDimensionHistory.id.notin_(keep_ids),
                )
            )


def submit_answers(db: Session, user_id: str, answers: dict[str, str]) -> dict:
    """提交三问答案 → 更新 user_profile.dimensions + 历史记录 + 复述文本

    返回 {dimensions, confirmation}（档案确认闭环的复述文本）
    """
    dims = _extract_answers(answers)

    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id, dimensions={}, version=1)
        db.add(profile)
    profile.dimensions = {**profile.dimensions, **dims}
    profile.version = (profile.version or 1) + 1
    # 审查修复(P1-15/R12)：updated_at 由 ORM onupdate 自动维护，不再手工赋值

    # 未命中枚举 → 扩展队列（不落画像维度）
    _queue_unmapped(db, user_id, answers, dims)

    # 历史记录（写入侧裁剪：每维度保留最近 10 条）
    for dim, values in dims.items():
        for v in values:
            db.add(ProfileDimensionHistory(user_id=user_id, dimension=dim, value=v))
    db.flush()  # 新行落 id/updated_at，供裁剪计算
    _trim_history(db, user_id, dims)
    # 审查修复(P1-15/R12)：三次 commit 收敛为单事务（原子性 + 少 2 次事务往返）
    db.commit()

    confirmation = (
        "我理解到：对你最重要的是{persons}；你经历过{turns}这样的人生转折；"
        "你最骄傲的是{proud}。这些了解对吗？"
    ).format(
        persons="、".join(dims["relation_core"]) or "（待补充）",
        turns="、".join(dims["life_events"]) or "（待补充）",
        proud="、".join(dims["values_priority"]) or "（待补充）",
    )
    return {"dimensions": dims, "confirmation": confirmation}


def get_profile(db: Session, user_id: str) -> dict:
    """查询画像（冷启动激活状态）"""
    profile = db.execute(
        select(UserProfile).where(UserProfile.user_id == user_id)
    ).scalar_one_or_none()
    return {
        "dimensions": profile.dimensions if profile else {},
        "version": profile.version if profile else 0,
        "cold_start_done": bool(profile and profile.dimensions),
    }
