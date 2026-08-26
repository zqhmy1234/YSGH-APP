"""纠错闭环 API（B5-c · F2）

POST /api/v1/corrections            —— 记录纠错（第①层数据源，需登录）
POST /api/v1/classify/arbitrate     —— 三层裁决分类（个人规则 → 全局 SetFit）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ERR_CORR_001, ERR_CORR_002, ERR_CORR_003, ERR_CORR_004, ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.correction import (
    ArbitrateRequest,
    CorrectionCreate,
    CorrectionOut,
)
from app.services.classifier import VALID_CLASSES
from app.services.correction import record_correction

router = APIRouter(prefix="/api/v1", tags=["corrections"])


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
    return ApiResponse(
        data=CorrectionOut(
            id=row.id,
            content_id=row.content_id,
            old_label=row.old_label,
            new_label=row.new_label,
            source=row.source,
        )
    )


@router.post("/classify/arbitrate", response_model=ApiResponse[dict])
def arbitrate_text(
    req: ArbitrateRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """三层裁决分类（P2-01 推理移 worker：SetFit ~27s 入队异步，返回 job_id 供轮询）"""
    from app.core.queue import enqueue_high, enqueue_idempotent
    from app.services.correction import arbitrate_job

    # TD-P3 M3（审查中危）：job.meta 写入 user_id —— 查询侧归属校验（防越权轮询他人结果）
    meta = {"user_id": str(user.id)}
    if req.client_request_id:
        # R4#4（提交端点幂等）：同 key 重复/并发提交返回同一 job，不重复入队
        job = enqueue_idempotent(
            "arbitrate", str(user.id), req.client_request_id,
            arbitrate_job, str(user.id), req.text, req.content_type, meta=meta,
        )
    else:
        job = enqueue_high(
            arbitrate_job, str(user.id), req.text, req.content_type, meta=meta,
        )
    return ApiResponse(data={"job_id": job.id, "status": "queued"})


@router.get("/classify/arbitrate/jobs/{job_id}", response_model=ApiResponse[dict])
def arbitrate_job_status(
    job_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """查询三层裁决任务状态与结果（轮询）

    TD-P3 M3：归属校验——job.meta.user_id 必须等于当前用户，否则 403；
    失败仅回传脱敏错误（不再直出 exc_info 末行）。
    """
    from app.core.queue import get_job

    job = get_job(job_id)
    if job is None:
        raise ApiError(ERR_CORR_003, "任务不存在或已过期", http=404)
    owner = (job.meta or {}).get("user_id")
    if owner is not None and str(owner) != str(user.id):
        raise ApiError(ERR_CORR_004, "任务不属于当前用户", http=403)
    status = job.get_status()
    if status == "finished":
        return ApiResponse(data={"job_id": job_id, "status": status, "result": job.return_value()})
    if status == "failed":
        return ApiResponse(
            data={"job_id": job_id, "status": status, "error": "任务执行失败，请稍后重试"}
        )
    return ApiResponse(data={"job_id": job_id, "status": status or "queued"})
