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
from app.core.config import settings
from app.core.errors import (
    ERR_UPLOAD_001,
    ERR_UPLOAD_002,
    ERR_UPLOAD_003,
    ERR_UPLOAD_004,
    ERR_UPLOAD_005,
    ERR_UPLOAD_006,
    ERR_UPLOAD_007,
    ERR_UPLOAD_008,
    ApiError,
)
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
    upload_mode: str = Form("original"),
    on_wifi: bool | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """建上传任务（client_upload_id 幂等）

    流量约束（B4 §6，Wave3 AgentG）：
      upload_mode: original（默认，完整原件）/ thumbnail_meta（蜂窝：只传缩略图+元数据）
      on_wifi: 客户端网络标记（可选，记录到内容 extra 供流量策略可观测）
    两参数仅契约/校验；最终语义以 complete 时 meta 为准（见 complete docstring）。
    """
    if upload_mode not in upload_svc.VALID_UPLOAD_MODES:
        raise ApiError(
            ERR_UPLOAD_007,
            f"upload_mode 非法（可选 {'/'.join(upload_svc.VALID_UPLOAD_MODES)}）",
            http=422,
        )
    try:
        task = upload_svc.init_upload(
            db, user.id, client_upload_id, file_name, file_size, chunk_size, storage
        )
    except ValueError as exc:
        raise ApiError(ERR_UPLOAD_001, str(exc), http=422) from exc
    return ApiResponse(data={
        "upload_id": task.id,
        "chunk_size": task.chunk_size,
        "chunk_count": task.chunk_count,
        "file_key": task.file_key,
        "status": task.status,
        "upload_mode": upload_mode,
        "on_wifi": on_wifi,
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
    return _upload_chunk_impl(upload_id, chunk_index, file, chunk_hash, db, user)


@router.post("/chunk", response_model=ApiResponse[dict])
def upload_chunk_post(
    upload_id: str = Form(...),
    chunk_index: int = Form(...),
    file: UploadFile = File(...),
    chunk_hash: str | None = Form(None),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """POST 别名（S-ST-1 · 2026-08-25）：uni-app x uni.uploadFile 不支持 PUT method，
    客户端分片上传只能 POST multipart；语义与 PUT 完全一致（幂等+校验）。"""
    return _upload_chunk_impl(upload_id, chunk_index, file, chunk_hash, db, user)


def _upload_chunk_impl(
    upload_id: str,
    chunk_index: int,
    file: UploadFile,
    chunk_hash: str | None,
    db: Session,
    user: User,
):
    data = file.file.read()
    try:
        result = upload_svc.upload_chunk(db, upload_id, chunk_index, data, chunk_hash, user_id=user.id)
    except KeyError as exc:
        raise ApiError(ERR_UPLOAD_002, str(exc), http=404) from exc
    except ValueError as exc:
        raise ApiError(ERR_UPLOAD_003, str(exc), http=422) from exc
    return ApiResponse(data=result)


@router.post("/complete", response_model=ApiResponse[dict])
def complete_upload(
    upload_id: str = Form(...),
    meta: str = Form("{}"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """合并分片 → 落最终对象 → 建/更新 contents 记录 + 入队管线（S-ST-1 集成）

    meta: JSON 字符串 {taken_at, gps_lat, gps_lng, source, extra,
                        upload_mode, on_wifi, content_id?}，语义与 /contents/upload 对齐。

    Wave3 AgentG（流量约束 B4 §6）：
      - upload_mode="thumbnail_meta"（蜂窝）：上传物即缩略图 → 只落 thumbnail_key
        占位内容（original_pending），不进管线；WiFi 后再用本端点补传原件。
      - upload_mode="original" + content_id=<占位 id>：手动立即上传原图——
        复用 complete 流程，把原件挂到既有占位内容并触发完整管线（与 photo 同链路）。
    """
    try:
        result = upload_svc.complete_upload(db, upload_id, user_id=user.id)
        # 集成：对象已在存储 → 建 contents 记录（cos_key）+ 入队 process_content
        content_id = upload_svc.register_photo_content(db, user.id, result["file_key"], meta)
        result["content_id"] = content_id
    except KeyError as exc:
        raise ApiError(ERR_UPLOAD_002, str(exc), http=404) from exc
    except ValueError as exc:
        raise ApiError(ERR_UPLOAD_004, str(exc), http=422) from exc
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
        raise ApiError(ERR_UPLOAD_002, str(exc), http=404) from exc


@router.get("/sts", response_model=ApiResponse[dict])
def upload_sts(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """客户端直传临时凭证（仅 cos 后端；STS 角色未就绪时给出降级提示）

    P0-2（审查 H2/S4-四-2）：
      - 生产环境 COS/STS 未配置 → 501（不返回假凭证；mock 凭证仅限非生产，
        get_settings 已强制生产 mock_external_ai=False 双保险）
      - 凭证按当前用户前缀签发（photos/voice/thumbnails/{user_id}/*），
        不再整桶通配
    """
    # 生产门控：COS/STS 未真配 → 501 显式告知走后端中转（防误配出假凭证）
    if settings.app_env == "production" and not _cos_sts_configured():
        raise ApiError(
            ERR_UPLOAD_008,
            "STS 直传未接入（生产未配置 COS/STS），请走后端中转上传",
            http=501,
        )
    backend = get_storage_backend()
    try:
        creds = backend.get_sts_credentials(user_id=str(user.id))
    except ValueError as exc:
        raise ApiError(ERR_UPLOAD_008, str(exc), http=501) from exc
    except NotImplementedError as exc:
        raise ApiError(ERR_UPLOAD_005, str(exc), http=501) from exc
    except Exception as exc:  # noqa: BLE001 —— STS 失败降级为后端中转
        # 审查修复(P1-03)：错误消息不泄漏内部异常类型（信息泄露面收敛）
        logger.warning("STS 获取失败: %s", exc)
        raise ApiError(ERR_UPLOAD_006, "STS 暂不可用，请走后端中转上传", http=503) from exc
    return ApiResponse(data=creds)


def _cos_sts_configured() -> bool:
    """COS/STS 直传配置就绪判定（生产门控用）：密钥/桶/地域/APPID/角色 ARN 齐全"""
    return bool(
        settings.tencent_secret_id
        and settings.tencent_secret_key
        and settings.cos_bucket
        and settings.cos_region
        and settings.tencent_appid
        and settings.tencent_sts_role_arn
    )
