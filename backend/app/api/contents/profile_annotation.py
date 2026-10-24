"""D-18 后端：画像标注池的**读 + 确认/否定**端点（详情页「诚实版 + 确认入口」所需）。

## 为什么需要这两个端点

用户选了「诚实版 + 待确认标注的确认入口」。但实测：`profile_annotation_pool` 表有
**308 条 pending**（全部 `status='pending'`），而**全仓没有任何 API 能读或改它** ——
只有 `services/profile_annotator.py` 在内部写。

⇒ 详情页要展示「AI 猜的、待你确认」，就必须先有这两个端点。

## 三个设计决定（都写进代码注释）

1. **只读 pending**：`GET` 默认 `status=pending`，不给前端「拉全部状态」的能力
   （那是运维/复盘需求，不该走用户端点）。
2. **确认 = 写进画像 + 标记 confirmed**：用户点「对」不是简单改个状态 ——
   要把该维度值真正写进 `user_profile.dimensions`（否则「确认了但画像没变」= 假状态）。
3. **否定 = 标记 discarded，不动画像**：用户说「不对」时，画像里本来就没有这条
   （pending 就是没进画像），故只需标记，避免下次再问。

## 与 `profile_sensitive` 的区别（别混）

· `profile_sensitive`（137 条）= **用户主动设的「别再提」保护数据** ⇒ 不展示、不确认；
· `profile_annotation_pool`（308 条）= **AI 推断的候选** ⇒ 展示、可确认/否定。
两者都在详情页附近出现，但语义完全相反 —— 本文件的端点只碰后者。
"""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import User
from app.db.models.profile import ProfileAnnotationPool, UserProfile
from app.db.session import get_db
from app.schemas.common import ApiResponse

logger = logging.getLogger("yishu.api.profile_annotation")

router = make_router(prefix="/api/v1/profile", tags=["profile-annotation"])

# 错误的 status 取值（与表注释的枚举一致；只暴露用户能操作的两个）
_ACTIONABLE = {"confirmed", "discarded"}


class AnnotationOut(BaseModel):
    """一条待确认标注（跨语言命名与客户端 `AnnotationItem` 对齐）。"""

    id: int
    dimension: str | None
    candidate_value: str | None
    confidence: float
    raw_text: str
    created_at: str | None


class AnnotationDecision(BaseModel):
    """确认/否定一条标注。"""

    action: str = Field(..., description="confirmed=对 / discarded=不对")
    # 确认时允许用户**改写**候选值（AI 猜「咖啡」，用户改成「奶茶」）。
    # 不改写则用原 candidate_value。
    value: str | None = Field(None, max_length=64, description="确认时可选：修正后的值")


@router.get("/annotations", response_model=ApiResponse[list[AnnotationOut]])
def list_annotations(
    limit: int = 50,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """列出**待确认**的画像标注（仅 pending；按置信度降序，高置信度更可能对）。"""
    if limit < 1 or limit > 200:
        raise ApiError("PROFILE_ANNOT_001", "limit 需在 1~200", http=422)
    rows = (
        db.execute(
            select(ProfileAnnotationPool)
            .where(
                ProfileAnnotationPool.user_id == str(user.id),
                ProfileAnnotationPool.status == "pending",
            )
            .order_by(ProfileAnnotationPool.confidence.desc(), ProfileAnnotationPool.id.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )
    return ApiResponse(
        data=[
            AnnotationOut(
                id=r.id,
                dimension=r.dimension,
                candidate_value=r.candidate_value,
                confidence=float(r.confidence or 0.0),
                raw_text=r.raw_text or "",
                created_at=r.created_at.isoformat() if r.created_at else None,
            )
            for r in rows
        ]
    )


@router.post("/annotations/{annotation_id}", response_model=ApiResponse[dict])
def decide_annotation(
    annotation_id: int,
    req: AnnotationDecision,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """确认（**写进画像**）或否定（只标记）一条待确认标注。

    ## 为什么确认必须写画像

    用户点「对」的语义是「这条关于我的描述是对的」⇒ 它应该出现在画像里。
    若只把 `status` 改成 `confirmed` 而不写 `user_profile.dimensions`，
    用户下次打开详情页会发现「我确认过了，但画像里没有」—— 那是假状态，
    正是本仓反复清理的那类问题。

    ## 实现要点

    · 只能操作**自己的** pending 记录（`user_id` 过滤 + `status='pending'` 双条件）；
    · 已是 confirmed/discarded ⇒ 409（不静默覆盖，用户可能重复点了）；
    · 维度为空 ⇒ 只标记不写画像（没有维度就没法写），并如实返回 `written=False`。
    """
    if req.action not in _ACTIONABLE:
        raise ApiError("PROFILE_ANNOT_002", f"action 必须为 {sorted(_ACTIONABLE)}", http=422)

    row = db.execute(
        select(ProfileAnnotationPool).where(
            ProfileAnnotationPool.id == annotation_id,
            ProfileAnnotationPool.user_id == str(user.id),
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError("PROFILE_ANNOT_003", "标注不存在", http=404)
    if row.status != "pending":
        raise ApiError(
            "PROFILE_ANNOT_004", f"该标注已处理（status={row.status}）", http=409
        )

    written = False
    if req.action == "discarded":
        row.status = "discarded"
    else:
        row.status = "confirmed"
        value = (req.value or row.candidate_value or "").strip()
        dim = (row.dimension or "").strip()
        if value and dim:
            # 写进画像：`dimensions[dim] = {value, source="user_confirmed", ...}`
            # 为什么 source 用 `user_confirmed` 而不是 annotation：详情页要靠它区分
            # 「AI 猜的」与「用户确认过的」，后者才该被当成事实展示。
            profile = db.execute(
                select(UserProfile).where(UserProfile.user_id == str(user.id))
            ).scalar_one_or_none()
            if profile is None:
                profile = UserProfile(user_id=str(user.id), version=1, dimensions={})
                db.add(profile)
            dims = dict(profile.dimensions or {})
            dims[dim] = {
                "value": value,
                "source": "user_confirmed",
                "confidence": 1.0,
                "strength": 1,
                "updated_at": datetime.now(UTC).isoformat(),
                "evidence": [{"text": (row.raw_text or "")[:200], "content_id": None}],
            }
            profile.dimensions = dims
            written = True

    db.commit()
    logger.info(
        "标注处置 user=%s id=%s action=%s written=%s", user.id, annotation_id, req.action, written
    )
    return ApiResponse(data={"id": annotation_id, "action": req.action, "written": written})
