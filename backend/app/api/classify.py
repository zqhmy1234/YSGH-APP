"""文字碎片分类 API（F2 · M1 Part 3：SetFit 5 类）

P2-01 重构（推理移 worker）：SetFit 单条 ~27s（CPU 实测），不再同步占用 API 线程池。
  POST /api/v1/classify          → 入队 high 队列，立即返回 {job_id}（API 不阻塞）
  GET  /api/v1/classify/jobs/{id} → 轮询 job 状态（queued/running/finished/failed）+ 结果

安全修复（审查 MAJOR）：分类消耗 BGE-M3/SetFit 算力，需登录防滥用。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.core.queue import enqueue_high, get_job
from app.db.models import User
from app.schemas.classify import ClassifyRequest, ClassifyResult
from app.schemas.common import ApiResponse
from app.services.classifier import classify_job

router = APIRouter(prefix="/api/v1/classify", tags=["classify"])


class ClassifyJobOut(BaseModel):
    job_id: str
    status: str
    result: ClassifyResult | None = None
    error: str | None = None


@router.post("", response_model=ApiResponse[dict])
def classify_text(
    req: ClassifyRequest,
    user: User = Depends(get_current_user),
):
    """文字碎片分类：入队异步执行（P2-01 推理移 worker），返回 job_id 供轮询"""
    job = enqueue_high(classify_job, req.text)
    return ApiResponse(data={"job_id": job.id, "status": "queued"})


@router.get("/jobs/{job_id}", response_model=ApiResponse[ClassifyJobOut])
def classify_job_status(
    job_id: str,
    user: User = Depends(get_current_user),
):
    """查询分类任务状态与结果（轮询；finished 时携带 result）"""
    job = get_job(job_id)
    if job is None:
        raise ApiError("CLASSIFY_002", "任务不存在或已过期", http=404)
    status = job.get_status()
    if status == "finished":
        result = job.return_value()
        return ApiResponse(
            data=ClassifyJobOut(job_id=job_id, status=status, result=ClassifyResult(**result))
        )
    if status == "failed":
        exc = job.exc_info.splitlines()[-1] if job.exc_info else "unknown"
        return ApiResponse(
            data=ClassifyJobOut(job_id=job_id, status=status, error=exc)
        )
    return ApiResponse(data=ClassifyJobOut(job_id=job_id, status=status or "queued"))
