"""画像管理路由：从已有记忆异步重建画像。"""
from fastapi import Depends

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_PROFILE_001, ApiError
from app.core.queue import QUEUE_LOW, enqueue_low, get_job
from app.db.models import User
from app.schemas.common import ApiResponse
from app.schemas.profile_rebuild import ProfileRebuildStartOut, ProfileRebuildStatusOut
from app.services.profile_rebuild import rebuild_user_profile

router = make_router(prefix="/api/v1/profile", tags=["profile"])


@router.post("/rebuild", response_model=ApiResponse[ProfileRebuildStartOut])
def start_profile_rebuild(user: User = Depends(get_current_user)):
    job = enqueue_low(
        rebuild_user_profile,
        str(user.id),
        job_timeout=3600,
        meta={"user_id": str(user.id), "queue": QUEUE_LOW, "status": "queued"},
    )
    return ApiResponse(data=ProfileRebuildStartOut(job_id=job.id))


@router.get(
    "/rebuild/jobs/{job_id}",
    response_model=ApiResponse[ProfileRebuildStatusOut],
)
def profile_rebuild_status(
    job_id: str,
    user: User = Depends(get_current_user),
):
    job = get_job(job_id)
    if job is None or str((job.meta or {}).get("user_id") or "") != str(user.id):
        # Not found / 非本人统一 404，不泄露 job 是否存在。
        raise ApiError(ERR_PROFILE_001, "画像重建任务不存在或已过期", http=404)
    status = job.get_status(refresh=True) or "queued"
    meta = dict(job.meta or {})
    error = None
    summary = {}
    if status == "failed":
        error = "任务执行失败，请稍后重试"
    elif status == "finished":
        try:
            summary = dict(job.return_value() or {})
        except Exception:  # noqa: BLE001 —— 结果过期/反序列化失败不 500
            summary = {}
    data = ProfileRebuildStatusOut(
        job_id=job_id,
        status=status,
        total=int(meta.get("total", summary.get("total", 0)) or 0),
        processed=int(meta.get("processed", summary.get("processed", 0)) or 0),
        applied=int(meta.get("applied", summary.get("applied", 0)) or 0),
        pooled=int(meta.get("pooled", summary.get("pooled", 0)) or 0),
        failed=int(meta.get("failed", summary.get("failed", 0)) or 0),
        skipped=int(meta.get("skipped", summary.get("skipped", 0)) or 0),
        degraded=bool(meta.get("degraded", summary.get("degraded", False))),
        error=error,
        summary=summary,
    )
    return ApiResponse(data=data)
