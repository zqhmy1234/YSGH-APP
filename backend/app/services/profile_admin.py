"""画像的**用户侧管理操作**（D07-12 · 2026-09-25 功能修复波 · 用户拍板「按建议来」）。

## 为什么需要

B1 定稿铁律是「**用户操作优先**：手动合并/确认后，自动算法永不覆盖」，但画像域
**一个写端点都没有**（只有 `GET /interview/profile` 与敏感话题的 POST/DELETE）——
用户看到不符的标签，改不了、确认不了、锁不了、**也删不掉**。`services/interview.py:14`
自注"B1-6 后续"，此后一直没做。

同时客户端 `PortraitPrivacyPanel` 的"清除画像数据"确认弹窗写着「将删除全部画像维度与
证据锚点，此操作不可恢复」，实际**只清了手机本地 storage**（代码注释与 toast 都如实标
"（本地）"，**没有伪造远端成功**）⇒ **弹窗那层是失实宣称**。

## 本批范围（拍板：只做"真删除"）

| 能力 | 本批 | 说明 |
|---|---|---|
| **清除画像**（真删服务端） | ✅ | 本模块 `clear_profile` + `DELETE /api/v1/interview/profile` |
| 改值 / 确认 / 锁定 | ⏸ 二期 | 要真兑现"人工优先"，须同时改 `profile_annotator` / `echo._profile_hit` 的
upsert 覆写语义（＝`D07-11`），**一处改动两处风险**，故单独立项 |

## 清除范围（**刻意保留保护性数据**）

删除：`user_profile`（维度值）、`profile_dimension_history`（历史值）、
`profile_dimension_pending`（未命中枚举的原始回答）、`profile_annotation_pool`
（低置信候选池，含用户原话）、`profile_l2_evidence`（证据锚点）。

**保留**：`profile_sensitive`（话题×处置：forbid/caution/…）—— 那是"**别再提这些话题**"
的**保护性数据**，清除画像不该顺手把保护也拆掉（用户会更受伤）。保留条数如实回报给
客户端，让用户知道"还有什么留下、为什么"。
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select, text

from app.db.models import (
    ProfileAnnotationPool,
    ProfileDimensionHistory,
    ProfileDimensionPending,
    ProfileSensitive,
    UserProfile,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import Session

logger = logging.getLogger("yishu.profile_admin")

# 事件级软话题回流/全局词不属"用户画像"，不在此清理范围（`sensitive_words` 归护栏域）
#
# 2026-10-24（待办总账 D-09 · 原台账 §7.26⑤）：本表此前**定义后无人引用** ——
# `clear_profile` 里手写五个 `cleared["<表名>"] = ...`，与这里的键各写一份。
# 后果不是立刻出错，而是「声明与实现可以悄悄分叉」：往 `clear_profile` 加了第六张表却
# 忘了写进本表（或反之），读代码的人会以为清理范围就是这五行。
# 现改为**机器校验**：`clear_profile` 末尾比对两边键集合，不一致即抛错。
# 这是本仓通用的「棘轮」做法（结构/基线只许收紧），代价是一次集合比较。
_CLEARED_TABLES = {
    "user_profile": "画像维度值",
    "profile_dimension_history": "维度历史值",
    "profile_dimension_pending": "未命中枚举的原始回答",
    "profile_annotation_pool": "低置信候选池（含证据原话）",
    "profile_l2_evidence": "证据锚点",
}


def clear_profile(db: Session, user_id: str) -> dict:
    """清除该用户的画像（**幂等**：全无数据时各计数为 0，不报错）。

    返回 `{"cleared": {表名: 删除行数}, "sensitive_topics_kept": N, "total": 总删除行数}`。

    ⚠️ `profile_l2_evidence` 只有 raw-SQL 写入方（`profile_annotator._write_l2_evidence`）、
    **无 ORM 模型** ⇒ 清除同样走 raw SQL，与该表现有口径一致（新增 ORM 模型会牵动
    `alembic check` 漂移面，属另一件事）。
    """
    cleared: dict[str, int] = {}

    cleared["user_profile"] = db.execute(
        sa_delete(UserProfile).where(UserProfile.user_id == user_id)
    ).rowcount or 0
    cleared["profile_dimension_history"] = db.execute(
        sa_delete(ProfileDimensionHistory).where(ProfileDimensionHistory.user_id == user_id)
    ).rowcount or 0
    cleared["profile_dimension_pending"] = db.execute(
        sa_delete(ProfileDimensionPending).where(ProfileDimensionPending.user_id == user_id)
    ).rowcount or 0
    cleared["profile_annotation_pool"] = db.execute(
        sa_delete(ProfileAnnotationPool).where(ProfileAnnotationPool.user_id == user_id)
    ).rowcount or 0
    cleared["profile_l2_evidence"] = db.execute(
        text("DELETE FROM profile_l2_evidence WHERE user_id = :uid"), {"uid": user_id}
    ).rowcount or 0

    kept = db.execute(
        select(func.count()).select_from(ProfileSensitive).where(ProfileSensitive.user_id == user_id)
    ).scalar_one()
    db.commit()

    # 单一来源校验（2026-10-24 · D-09）：声明表集合必须与实际清理的表**完全相同**。
    # 只在开发/测试期能靠代码评审保证，这里做成运行期棘轮——漏登记即报错而不是静默少清。
    # 注意用 set 比较而非 `list(cleared)` 顺序比较：顺序无语义（返回 dict 的键序只是插入序）。
    decl, actual = set(_CLEARED_TABLES), set(cleared)
    if decl != actual:
        raise RuntimeError(
            f"clear_profile 与 _CLEARED_TABLES 不一致："
            f"声明未清={sorted(decl - actual)} 清理未声明={sorted(actual - decl)}"
        )

    total = sum(cleared.values())
    logger.info(
        "画像已清除 user=%s 删除 %d 行（%s），保留敏感话题 %d 条",
        user_id,
        total,
        ", ".join(f"{k}={v}" for k, v in cleared.items()),
        kept,
    )
    return {
        "cleared": cleared,
        "sensitive_topics_kept": int(kept or 0),
        "total": total,
    }
