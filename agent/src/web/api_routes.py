"""
忆光 Web 前端 REST API 路由

提供记忆浏览、知识收藏浏览、统计概览等数据接口。
前端页面通过这些 API 获取数据，对话功能复用 /stream_run 接口。
"""
import logging
from datetime import datetime, timedelta
from platform.context import new_context, request_context

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse
from storage.database.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# L1-A 记忆分类
MEMORY_CATEGORIES = ["全部", "情绪", "事件", "想法", "灵感", "人物", "地点", "待分类"]

# L1-B 收藏分类
COLLECTION_TYPES = ["全部", "文章", "帖子", "观点", "数据", "图片", "其他"]


def _get_user_id_from_request(request: Request) -> str:
    """从请求中获取用户ID（优先 header，兜底查询参数，再兜底环境变量）"""
    user_id = request.headers.get("X-User-Id") or request.query_params.get("user_id")
    if not user_id:
        ctx = request_context.get()
        if ctx and ctx.user_id:
            return ctx.user_id
        import os
        return os.getenv("COZE_USER_ID", "default")
    return user_id


def _ensure_ctx(user_id: str):
    """设置 request_context，确保工具函数能获取到 user_id"""
    ctx = new_context(method="web_api")
    ctx.user_id = user_id
    request_context.set(ctx)


@router.get("/dashboard")
async def get_dashboard(request: Request):
    """首页统计数据：总记忆数、总收藏数、情绪分布、本周新增"""
    user_id = _get_user_id_from_request(request)
    _ensure_ctx(user_id)
    client = get_supabase_client()

    try:
        # 总记忆数
        mem_resp = client.table("memories").select("id", count="exact").eq("user_id", user_id).execute()
        total_memories = mem_resp.count or 0

        # 总收藏数
        col_resp = client.table("knowledge_collections").select("id", count="exact").eq("user_id", user_id).execute()
        total_collections = col_resp.count or 0

        # 情绪分布
        mood_resp = client.table("memories").select("mood").eq("user_id", user_id).execute()
        mood_counts = {}
        for item in (mood_resp.data or []):
            mood = item.get("mood") or "未标记"
            mood_counts[mood] = mood_counts.get(mood, 0) + 1

        # 主题分布
        topics_resp = client.table("memories").select("topics").eq("user_id", user_id).execute()
        topic_counts = {}
        for item in (topics_resp.data or []):
            mem_topics = item.get("topics")
            if isinstance(mem_topics, list):
                for t in mem_topics:
                    topic_counts[t] = topic_counts.get(t, 0) + 1

        # 本周新增（记忆+收藏）
        week_ago = (datetime.utcnow() - timedelta(days=7)).isoformat()
        week_mem = client.table("memories").select("id", count="exact").eq("user_id", user_id).gte("created_at", week_ago).execute()
        week_col = client.table("knowledge_collections").select("id", count="exact").eq("user_id", user_id).gte("created_at", week_ago).execute()
        week_new = (week_mem.count or 0) + (week_col.count or 0)

        return JSONResponse({
            "success": True,
            "data": {
                "total_memories": total_memories,
                "total_collections": total_collections,
                "mood_distribution": mood_counts,
                "topic_distribution": topic_counts,
                "week_new": week_new,
            }
        })
    except Exception as e:
        logger.error(f"获取仪表盘数据失败: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.get("/memories")
async def get_memories(
    request: Request,
    category: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """记忆列表（分页 + 分类筛选）"""
    user_id = _get_user_id_from_request(request)
    _ensure_ctx(user_id)
    client = get_supabase_client()

    try:
        offset = (page - 1) * page_size
        query = client.table("memories").select(
            "id, content, summary, mood, topics, location, people, category_id, created_at, memory_categories(name)",
            count="exact"
        ).eq("user_id", user_id)

        if category and category != "全部":
            query = query.eq("memory_categories.name", category)

        query = query.order("created_at", desc=True).range(offset, offset + page_size - 1)
        response = query.execute()

        items = []
        for mem in (response.data or []):
            cat_data = mem.get("memory_categories")
            cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
            items.append({
                "id": mem.get("id"),
                "content": mem.get("content"),
                "summary": mem.get("summary"),
                "mood": mem.get("mood"),
                "topics": mem.get("topics") or [],
                "location": mem.get("location"),
                "people": mem.get("people") or [],
                "category": cat_name,
                "created_at": mem.get("created_at"),
            })

        total = response.count or 0
        return JSONResponse({
            "success": True,
            "data": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            }
        })
    except Exception as e:
        logger.error(f"获取记忆列表失败: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.get("/collections")
async def get_collections(
    request: Request,
    content_type: str | None = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    """知识收藏列表（分页 + 分类筛选）"""
    user_id = _get_user_id_from_request(request)
    _ensure_ctx(user_id)
    client = get_supabase_client()

    try:
        offset = (page - 1) * page_size
        query = client.table("knowledge_collections").select("*", count="exact").eq("user_id", user_id)

        if content_type and content_type != "全部":
            query = query.eq("content_type", content_type)

        query = query.order("created_at", desc=True).range(offset, offset + page_size - 1)
        response = query.execute()

        items = []
        for col in (response.data or []):
            items.append({
                "id": col.get("id"),
                "title": col.get("title") or "无标题",
                "content": col.get("content"),
                "summary": col.get("summary"),
                "content_type": col.get("content_type"),
                "author": col.get("author"),
                "source": col.get("source"),
                "topics": col.get("topics") or [],
                "created_at": col.get("created_at"),
            })

        total = response.count or 0
        return JSONResponse({
            "success": True,
            "data": items,
            "pagination": {
                "page": page,
                "page_size": page_size,
                "total": total,
                "total_pages": (total + page_size - 1) // page_size,
            }
        })
    except Exception as e:
        logger.error(f"获取收藏列表失败: {e}")
        return JSONResponse({"success": False, "message": str(e)}, status_code=500)


@router.get("/categories")
async def get_categories():
    """获取分类列表"""
    return JSONResponse({
        "success": True,
        "memory_categories": MEMORY_CATEGORIES,
        "collection_types": COLLECTION_TYPES,
    })
