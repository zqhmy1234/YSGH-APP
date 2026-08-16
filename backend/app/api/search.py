"""检索路由：描述性搜索（B2 RAG，F5）+ 溯源"""
import time

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.search import SearchHit, SearchQuery, SearchResult

router = APIRouter(prefix="/api/v1/search", tags=["search"])


@router.post("", response_model=ApiResponse[SearchResult])
def search(req: SearchQuery, db: Session = Depends(get_db)):
    """描述性搜索主链路（API-003）：
    query → LLM 改写/路由 → 多路召回（Qdrant dense+sparse RRF）→ payload filter
    → bge-reranker 粗排 → qwen-flash 精排 → 溯源。

    Mock：返回确定性占位结果，契约消费方可走通搜索页 UI 流程。
    验收：Top3≥70% + P95<3s（M1 门禁，RET-014/RET-018）。
    """
    start = time.perf_counter()
    hits = [
        SearchHit(
            content_id="mock-hit-0001",
            content_type="photo",
            text="杭州西湖 · 2025 夏（mock）",
            taken_at=None,
            place="杭州",
            event_id="mock-event-0001",
            event_title="7月杭州之旅",
            score=0.92,
            trace={"matched": ["place", "text"], "event": "7月杭州之旅"},
        ),
        SearchHit(
            content_id="mock-hit-0002",
            content_type="text",
            text="考研备考笔记（mock）",
            taken_at=None,
            place=None,
            event_id="mock-event-0002",
            event_title="2026 考研备考",
            score=0.85,
            trace={"matched": ["text"], "event": "2026 考研备考"},
        ),
    ]
    latency_ms = int((time.perf_counter() - start) * 1000)
    return ApiResponse(
        data=SearchResult(
            query=req.q,
            rewritten_query=f"<rewrite:{req.q}>",
            intent="text",
            hits=hits[: req.limit],
            total=len(hits),
            latency_ms=latency_ms,
        )
    )
