"""检索路由：描述性搜索（B2 RAG，F5）+ 溯源"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import SearchQuery, SearchResult
from app.services.rag import search as rag_search

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("", response_model=ApiResponse[SearchResult])
def search(req: SearchQuery, db: Session = Depends(get_db)):
    """描述性搜索主链路（API-003）：
    query → LLM 改写/路由 → 多路召回（Qdrant dense+sparse RRF）→ payload filter
    → bge-reranker 粗排 → qwen-flash 精排 → 溯源。

    验收：Top3≥70% + P95<3s（M1 门禁，RET-014/RET-018）。
    """
    return ApiResponse(data=rag_search(req, db=db))
