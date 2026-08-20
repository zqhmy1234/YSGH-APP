"""检索路由：描述性搜索（B2 RAG，F5）+ 溯源 + 以图搜图（B2-4）"""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import SearchQuery, SearchResult
from app.services.rag import search as rag_search
from app.services.rag import search_by_image

router = APIRouter(prefix="/api/v1/search", tags=["search"])

# 图片上传上限（以图搜图查询图）
_MAX_IMAGE_BYTES = 10 * 1024 * 1024


@router.post("/image", response_model=ApiResponse[SearchResult])
def search_by_image_api(
    file: UploadFile = File(..., description="查询图片（jpg/png，≤10MB）"),
    limit: int = Query(10, ge=1, le=50),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """以图搜图（B2-4）：上传图片 → Qwen3-VL caption → image_vec 相似检索

    查询向量 = BGE-M3(caption)（caption 向量化方案）；返回与描述性搜索同构结果。
    """
    data = file.file.read()
    if len(data) == 0:
        raise ApiError("SEARCH_001", "空图片文件", http=422)
    if len(data) > _MAX_IMAGE_BYTES:
        raise ApiError("SEARCH_002", f"图片超过 {_MAX_IMAGE_BYTES // 1024 // 1024}MB 上限", http=422)
    suffix = Path(file.filename or "query.jpg").suffix or ".jpg"
    with NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(data)
        tmp_path = tmp.name
    try:
        result = search_by_image(tmp_path, SearchQuery(q="[image]", limit=limit), db=db, user_id=user.id)
    finally:
        Path(tmp_path).unlink(missing_ok=True)
    return ApiResponse(data=result)


@router.post("", response_model=ApiResponse[SearchResult])
def search(
    req: SearchQuery,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """描述性搜索主链路（API-003）：
    query → LLM 改写/路由 → 多路召回（Qdrant dense+sparse RRF）→ payload filter
    → bge-reranker 粗排 → qwen-flash 精排 → 溯源。

    验收：Top3≥70% + P95<3s（M1 门禁，RET-014/RET-018）。

    安全修复（审查 MAJOR）：个人记忆检索必须登录后可用（此前未鉴权）。
    """
    return ApiResponse(data=rag_search(req, db=db, user_id=user.id))
