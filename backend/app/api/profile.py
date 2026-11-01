"""画像管理路由：画像重建 + 锁定标签（B1:139）。"""
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_PROFILE_001, ERR_PROFILE_002, ApiError
from app.core.queue import QUEUE_LOW, enqueue_low, get_job
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.profile_locked_tags import ProfileLockedTagsIn, ProfileLockedTagsOut
from app.schemas.profile_rebuild import ProfileRebuildStartOut, ProfileRebuildStatusOut
from app.services.profile_locked_tags import get_locked_tags, replace_locked_tags
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
        # 2026-10-30（P2 F-6）：批次进度（批大小由 worker 的 limit 决定，不再兼作总量上限）
        batches=int(meta.get("batches", summary.get("batches", 0)) or 0),
        degraded=bool(meta.get("degraded", summary.get("degraded", False))),
        error=error,
        summary=summary,
    )
    return ApiResponse(data=data)


# ---------------------------------------------------------------- 锁定标签（B1:139）
#
# 真源在服务端（user_profile.locked_tags）。客户端 TagEditPanel 的「固定」原先只写本机
# storage ⇒ 后端不知情，画像重建时 AI 会把用户删掉/固定的标签写回来。这两个端点把它
# 变成服务端事实，`services/profile_annotator.apply_annotation` 在写回前按该集合跳过。


@router.get("/locked-tags", response_model=ApiResponse[ProfileLockedTagsOut])
def locked_tags_read(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """读锁定标签集合。从未设置过的用户返回空数组（不建画像空行）。"""
    return ApiResponse(data=ProfileLockedTagsOut(tags=get_locked_tags(db, user.id)))


@router.put("/locked-tags", response_model=ApiResponse[ProfileLockedTagsOut])
def locked_tags_replace(
    req: ProfileLockedTagsIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """整体覆盖锁定标签集合（空数组 = 全部解锁），返回规整后的最新集合。"""
    try:
        tags = replace_locked_tags(db, user.id, req.tags)
    except ValueError as exc:
        # 非法项不静默丢弃：否则"PUT 成功但集合变了"无从解释（PROFILE_002 / 422）
        raise ApiError(ERR_PROFILE_002, str(exc), http=422) from exc
    return ApiResponse(data=ProfileLockedTagsOut(tags=tags))
