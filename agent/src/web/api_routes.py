"""
忆光 Web 前端 REST API 路由

提供记忆浏览、知识收藏浏览、统计概览等数据接口。
前端页面通过这些 API 获取数据，对话功能复用 /stream_run 接口。
"""
import logging
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, Header, Query, Request
from fastapi.responses import JSONResponse
from storage.database.supabase_client import get_supabase_client
from yishu.context import new_context, request_context
from yishu.security import RateLimiter, require_token, validate_user_id

logger = logging.getLogger(__name__)

# L1-A 记忆分类
MEMORY_CATEGORIES = ["全部", "情绪", "事件", "想法", "灵感", "人物", "地点", "待分类"]

# L1-B 收藏分类
COLLECTION_TYPES = ["全部", "文章", "帖子", "观点", "数据", "图片", "其他"]

# 限流器（进程内，按 user_id 滑动窗口；实例化一次随 app 生命周期）
_RATE_LIMITER = RateLimiter.from_env()


def _category_id(client, name: str):
    """分类名 → `category_id`（2026-10-05）。

    `memories` 表存的是 `category_id`，分类名在 `memory_categories.name`。
    与 `tools/memory_tools._category_id` 是**同一件事**的两处实现——之所以各留一份，
    是因为这一层（web 面）不能 import 工具层（工具层会拉起 langchain 工具注册）。
    改动时两处必须一起改。

    ⚠️ 为什么不能用 PostgREST 的**关系路径过滤**（`eq("memory_categories.name", x)`）：
    本仓数据层是 `local_client` 薄层，没有括号的 `a.b` 会被当作**列名**直拼
    （`getattr(Memories, "memory_categories.name")` → None），于是抛
    `ValueError: 表 memories 上不存在列 ...` ⇒ 该端点**必然 500**
    （被下面的 `except` 转成 500 响应，属在线故障）。实测确认。
    """
    try:
        resp = (
            client.table("memory_categories").select("id").eq("name", name).limit(1).execute()
        )
        rows = getattr(resp, "data", None)
        return rows[0].get("id") if rows else None
    except Exception as exc:  # noqa: BLE001 —— 取 id 失败不该阻断列表查询
        logger.warning("分类查询失败: %s", exc)
        return None


def _error_response(context: str, exc: Exception, *, status: int = 500) -> JSONResponse:
    """统一的错误响应：**把异常原文留在日志、不回显给调用方**（2026-10-05）。

    ## 为什么必须统一收口

    `str(e)` 里可能有数据库 DSN（含密码）、服务器绝对路径、SQL 片段、内部表/列名。
    这个响应体会被前端展示、并进入我们后端的日志。`main.py` 早已按这条口径
    收口（见那里的注释），但本 web 面的 3 个端点**漏了**——同一仓库两套口径，
    正是"加固不会漏掉旁路"的反面案例，故收敛成一个函数。

    排查所需细节由 `logger.exception` 完整落盘，日志侧不丢信息。
    """
    logger.exception("%s 失败", context)
    return JSONResponse(
        {"success": False, "message": f"{context}失败，请稍后重试"},
        status_code=status,
    )


async def require_auth(
    request: Request,
    x_agent_token: str | None = Header(default=None),
) -> str:
    """**本router 全局鉴权 + 限流**（2026-10-04 安全加固）。

    为什么必须加：此前的 4 个端点（`/dashboard` `/memories` `/collections` `/categories`）
    **完全没有 token 校验**，而 `user_id` 取自客户端可控的
    `X-User-Id` header 或 `?user_id=` 查询参数，于是：

        curl "http://<host>:8300/api/memories?user_id=<受害者的 uuid>"

    即可读到**任何人**的全部记忆（原文、情绪、标签、位置）。它不报错、
    只会安静地泄漏数据——这是本项目目前最严重的越权点。

    用 `dependencies=[Depends(require_auth)]` 挂到 router 上而不是逐个端点重复，
    是为了**新加端点时默认受保护**（逐个加的必然漏）。
    """
    require_token(x_agent_token)
    user_id = _get_user_id_from_request(request)
    user_id = validate_user_id(user_id)
    allowed, _remaining = _RATE_LIMITER.check(user_id)
    if not allowed:
        from fastapi import HTTPException

        raise HTTPException(status_code=429, detail="请求过于频繁，请稍后再试")
    _ensure_ctx(user_id)
    return user_id


# 挂到整个 router：新端点自动受保护
router = APIRouter(prefix="/api", dependencies=[Depends(require_auth)])


def _get_user_id_from_request(request: Request) -> str:
    """取 user_id：header → 查询参数 →（已由 require_auth 注入的）上下文。

    ⚠️ 2026-10-04 删除了原来的 `os.getenv("COZE_USER_ID", "default")` 兜底。
    那个兜底与 `memory_tools` / `url_fetch_tools` 的同款漏洞一致：上下文缺失时
    落到**全局共享**身份，于是「拿不到用户」不是报错，而是**读到别人数据**。
    现在缺失即由 `validate_user_id` 返 400 —— fail-closed。
    """
    user_id = request.headers.get("X-User-Id") or request.query_params.get("user_id")
    if user_id:
        return user_id.strip()
    ctx = request_context.get()
    if ctx and ctx.user_id:
        return ctx.user_id
    return ""


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
        return _error_response("获取仪表盘数据", e)


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
            # 关系路径过滤在本薄层必抛 ValueError（见 `_category_id` 说明），
            # 改为「先查 id 再按 category_id 过滤」。
            cat_id = _category_id(client, category)
            if cat_id is not None:
                query = query.eq("category_id", cat_id)
            else:
                # 分类名不存在 ⇒ 该分类下必然无数据，直接返回空集，
                # 而不是退化成"不过滤"（那会把全部记忆当成该分类返回）
                return JSONResponse({
                    "success": True,
                    "data": [],
                    "pagination": {"page": page, "page_size": page_size, "total": 0, "total_pages": 0},
                })

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
        return _error_response("获取记忆列表", e)


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
        return _error_response("获取收藏列表", e)


@router.get("/categories")
async def get_categories():
    """获取分类列表"""
    return JSONResponse({
        "success": True,
        "memory_categories": MEMORY_CATEGORIES,
        "collection_types": COLLECTION_TYPES,
    })
