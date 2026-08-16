"""内容路由：四类素材上传主链路（API-002）+ 相册直传（COS STS，决策 #10）"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.content import (
    ContentCreate,
    ContentOut,
    ContentUploadResult,
    CosPresign,
)

router = APIRouter(prefix="/api/v1/contents", tags=["contents"])

# Mock 数据源（契约消费方联调用；接入 PG 后替换）
_MOCK_CONTENTS: list[dict] = []


@router.post("", response_model=ApiResponse[ContentOut])
def create_content(req: ContentCreate, db: Session = Depends(get_db)):
    """内容入库：POST → 入库 → 异步 AI 管线（RQ）→ 状态回写（API-002/API-016）"""
    # TODO(T1): contents 表 INSERT + 去重（perceptual_hash）+ RQ 入队（转写/OCR/分类/聚类）
    if req.content_type not in ("photo", "text", "voice", "article"):
        raise ApiError("CONTENT_001", "不支持的 content_type", http=422)

    record = {
        "id": f"mock-content-{len(_MOCK_CONTENTS) + 1:04d}",
        "content_type": req.content_type,
        "content_class": None,
        "text": req.text,
        "taken_at": req.taken_at or datetime.now(timezone.utc),
        "place": None,
        "emotion": None,
        "tags": [],
        "status": "processing",   # AI 管线完成后回写 done（异步）
        "created_at": datetime.now(timezone.utc),
    }
    _MOCK_CONTENTS.append(record)
    return ApiResponse(data=ContentOut(**record))


@router.post("/presign", response_model=ApiResponse[ContentUploadResult])
def presign_upload(db: Session = Depends(get_db)):
    """照片直传：后端签 STS 临时密钥（30 秒有效）→ 客户端直传 COS（决策 #10/SYNC-013）"""
    # TODO(T1): 腾讯云 STS 接口；M1 验证 STS 最短有效期限制
    expire = datetime.now(timezone.utc) + timedelta(seconds=30)
    return ApiResponse(
        data=ContentUploadResult(
            content_id="mock-content-presign",
            status="ready",
            cos_presign=CosPresign(
                tmp_secret_id="mock-secret-id",
                tmp_secret_key="mock-secret-key",
                session_token="mock-session-token",
                expired_at=expire,
                cos_key=f"photos/mock/{expire.timestamp():.0f}.jpg",
            ),
        )
    )


@router.get("", response_model=ApiResponse[list[ContentOut]])
def list_contents(limit: int = 20, cursor: str | None = None, db: Session = Depends(get_db)):
    """内容分页列表（API-006 游标）"""
    items = [ContentOut(**c) for c in _MOCK_CONTENTS[-limit:]]
    return ApiResponse(data=items)
