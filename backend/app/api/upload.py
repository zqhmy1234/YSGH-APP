"""上传路由（S5-03 分片上传/断电续传 · WP-C 2026-08-19）

- POST /api/v1/upload/init       建任务（client_upload_id 幂等）
- PUT  /api/v1/upload/chunk      传单片（幂等 + SHA256 校验）
- POST /api/v1/upload/complete   合并落最终对象（幂等）
- GET  /api/v1/upload/status     断点续传状态（已传/缺失分片）
- GET  /api/v1/upload/sts        客户端直传临时凭证（cos 后端；失败降级提示）
"""
import logging

from fastapi import APIRouter, Depends, File, Form, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.services import upload as upload_svc
from app.services.external.storage import get_storage_backend

logger = logging.getLogger("yishu.upload")

router = APIRouter(prefix="/api/v1/upload", tags=["upload"])


@router.post("/init", response_model=ApiResponse[dict])
def init_upload(
    client_upload_id: str = Form(...),
    file_name: str = Form(...),
    file_size: int = Form(...),
    chunk_size: int = Form(upload_svc.DEFAULT_CHUNK_SIZE),
    storage: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        task = upload_svc.init_upload(
            db, user.id, client_upload_id, file_name, file_size, chunk_size, storage
        )
    except ValueError as exc:
        raise ApiError("UPLOAD_001", str(exc), http=422) from exc
    return ApiResponse(data={
        "upload_id": task.id,
        "chunk_size": task.chunk_size,
        "chunk_count": task.chunk_count,
        "file_key": task.file_key,
        "status": task.status,
    })


@router.put("/chunk", response_model=ApiResponse[dict])
def upload_chunk(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    chunk_hash: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    data = file.file.read()
    try:
        result = upload_svc.upload_chunk(db, upload_id, chunk_index, data, chunk_hash, user_id=user.id)
    except KeyError as exc:
        raise ApiError("UPLOAD_002", str(exc), http=404) from exc
    except ValueError as exc:
        raise ApiError("UPLOAD_003", str(exc), http=422) from exc
    return ApiResponse(data=result)


@router.post("/complete", response_model=ApiResponse[dict])
def complete_upload(
    upload_id: str = Form(...),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        result = upload_svc.complete_upload(db, upload_id, user_id=user.id)
    except KeyError as exc:
        raise ApiError("UPLOAD_002", str(exc), http=404) from exc
    except ValueError as exc:
        raise ApiError("UPLOAD_004", str(exc), http=422) from exc
    return ApiResponse(data=result)


@router.get("/status", response_model=ApiResponse[dict])
def upload_status(
    upload_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    try:
        return ApiResponse(data=upload_svc.get_status(db, upload_id, user_id=user.id))
    except KeyError as exc:
        raise ApiError("UPLOAD_002", str(exc), http=404) from exc


@router.get("/sts", response_model=ApiResponse[dict])
def upload_sts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """客户端直传临时凭证（仅 cos 后端；STS 角色未就绪时给出降级提示）"""
    backend = get_storage_backend()
    try:
        creds = backend.get_sts_credentials()
    except NotImplementedError as exc:
        raise ApiError("UPLOAD_005", str(exc), http=501) from exc
    except Exception as exc:  # noqa: BLE001 —— STS 失败降级为后端中转
        # 审查修复(P1-03)：错误消息不泄漏内部异常类型（信息泄露面收敛）
        logger.warning("STS 获取失败: %s", exc)
        raise ApiError("UPLOAD_006", "STS 暂不可用，请走后端中转上传", http=503) from exc
    return ApiResponse(data=creds)
