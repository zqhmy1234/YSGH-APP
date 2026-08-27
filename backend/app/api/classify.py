"""文字碎片分类 + 三层裁决 API（F2 · M1 Part 3：SetFit 5 类 · B5-c）

P2-01 重构（推理移 worker）：SetFit 单条 ~27s（CPU 实测），不再同步占用 API 线程池。
  POST /api/v1/classify                   → 入队 high 队列，立即返回 {job_id}（API 不阻塞）
  GET  /api/v1/classify/jobs/{id}         → 轮询 job 状态（queued/running/finished/failed）+ 结果
  POST /api/v1/classify/arbitrate         → 三层裁决分类（个人规则 → 全局 SetFit）
  GET  /api/v1/classify/arbitrate/jobs/{id} → 裁决任务状态 + 结果

R4#9（arbitrate 路由归属）：arbitrate 裁决归属 /classify 域——路径与 OpenAPI 契约
"分类与裁决"分组一致，从 corrections.py 迁入本模块；实现服务仍为 services.correction.arbitrate_job。

安全修复（审查 MAJOR）：分类/裁决消耗 BGE-M3/SetFit 算力，需登录防滥用。
"""
from fastapi import Depends

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import (
    ERR_CLASSIFY_002,
    ERR_CLASSIFY_003,
    ERR_CORR_003,
    ERR_CORR_004,
    ApiError,
)
from app.core.queue import enqueue_high, enqueue_idempotent, get_job
from app.db.models import User
from app.schemas.classify import (
    ArbitrateJobOut,
    ArbitrateJobQueued,
    ClassifyJobOut,
    ClassifyJobQueued,
    ClassifyRequest,
    ClassifyResult,
)
from app.schemas.common import ApiResponse
from app.schemas.correction import ArbitrateRequest
from app.services.classifier import classify_job

router = make_router(prefix="/api/v1/classify", tags=["classify"])


@router.post("", response_model=ApiResponse[ClassifyJobQueued])
def classify_text(
    req: ClassifyRequest,
    user: User = Depends(get_current_user),
):
    """文字碎片分类：入队异步执行（P2-01 推理移 worker），返回 job_id 供轮询"""
    # TD-P3 M3（审查中危）：job.meta 写入 user_id —— 查询侧据此做归属校验（防越权轮询他人结果）
    if req.client_request_id:
        # R4#4（提交端点幂等）：同 key 重复/并发提交返回同一 job，不重复入队
        job = enqueue_idempotent(
            "classify", str(user.id), req.client_request_id,
            classify_job, req.text, meta={"user_id": str(user.id)},
        )
    else:
        job = enqueue_high(classify_job, req.text, meta={"user_id": str(user.id)})
    return ApiResponse(data={"job_id": job.id, "status": "queued"})


@router.get("/jobs/{job_id}", response_model=ApiResponse[ClassifyJobOut])
def classify_job_status(
    job_id: str,
    user: User = Depends(get_current_user),
):
    """查询分类任务状态与结果（轮询；finished 时携带 result）

    TD-P3 M3：归属校验——job.meta.user_id 必须等于当前用户，否则 403；
    失败仅回传脱敏错误（不再直出 exc_info 末行，防内部路径/堆栈泄漏）。
    """
    job = get_job(job_id)
    if job is None:
        raise ApiError(ERR_CLASSIFY_002, "任务不存在或已过期", http=404)
    owner = (job.meta or {}).get("user_id")
    if owner is not None and str(owner) != str(user.id):
        raise ApiError(ERR_CLASSIFY_003, "任务不属于当前用户", http=403)
    status = job.get_status()
    if status == "finished":
        result = job.return_value()
        return ApiResponse(
            data=ClassifyJobOut(job_id=job_id, status=status, result=ClassifyResult(**result))
        )
    if status == "failed":
        return ApiResponse(
            data=ClassifyJobOut(job_id=job_id, status=status, error="任务执行失败，请稍后重试")
        )
    return ApiResponse(data=ClassifyJobOut(job_id=job_id, status=status or "queued"))


@router.post("/arbitrate", response_model=ApiResponse[ArbitrateJobQueued])
def arbitrate_text(
    req: ArbitrateRequest,
    user: User = Depends(get_current_user),
):
    """三层裁决分类（R4#9：归属 /classify 域；P2-01 推理移 worker：SetFit ~27s 入队异步）"""
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


@router.get("/arbitrate/jobs/{job_id}", response_model=ApiResponse[ArbitrateJobOut])
def arbitrate_job_status(
    job_id: str,
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
