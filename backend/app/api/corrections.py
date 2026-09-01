"""纠错闭环 API（B5-c · F2）

POST /api/v1/corrections —— 记录纠错（第①层数据源，需登录）

R4#9（arbitrate 路由归属）：三层裁决（/api/v1/classify/arbitrate）已迁入
api/classify.py（/classify 域，与 OpenAPI"分类与裁决"分组一致）；本模块只留纠错闭环。
"""
from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import (
    ERR_CORR_001,
    ERR_CORR_002,
    ERR_EVENT_005,
    ApiError,
)
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.correction import CorrectionCreate, CorrectionOut
from app.services.classifier import VALID_CLASSES
from app.services.correction import record_correction

router = make_router(prefix="/api/v1", tags=["corrections"])


@router.post("/corrections", response_model=ApiResponse[CorrectionOut])
def create_correction(
    req: CorrectionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    if req.new_label not in VALID_CLASSES:
        raise ApiError(ERR_CORR_001, f"new_label 必须为 {sorted(VALID_CLASSES)} 之一", http=422)
    if req.source not in ("active", "echo", "org"):
        raise ApiError(ERR_CORR_002, "source 必须为 active/echo/org", http=422)
    # R6#13（安全纵深）：content_id 归属校验——写前查 contents 表存在且属当前用户，
    # 拒绝跨用户 content_id 写纠错记录（对齐 sync.push content_owner 预载；404 防 IDOR/脏引用）
    content = db.scalar(select(Content).where(Content.id == req.content_id))
    if content is None or str(content.user_id) != str(user.id):
        raise ApiError(ERR_EVENT_005, "内容不存在或不属于当前用户", http=404)
    row = record_correction(
        db,
        user_id=user.id,
        content_id=req.content_id,
        text=req.text,
        new_label=req.new_label,
        old_label=req.old_label,
        source=req.source,
        content_type=req.content_type,
    )
    # D-22（08-29，P1 回写）：主动纠错即时同步权威字段 contents.content_class——
    # 此前 corrections 只写 correction_log、从不回写，内容分类展示永久停旧值。
    # 仅 active 来源回写；echo/org 属被动信号，须过服务层三道闸门后才影响层①，不动权威字段。
    if req.source == "active" and content.content_class != req.new_label:
        content.content_class = req.new_label
        db.commit()
    return ApiResponse(
        data=CorrectionOut(
            id=row.id,
            content_id=row.content_id,
            old_label=row.old_label,
            new_label=row.new_label,
            source=row.source,
        )
    )
