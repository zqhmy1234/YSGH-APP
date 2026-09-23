"""
忆光 - 记忆管理工具
提供记忆的存储、搜索、分类等功能
"""
import json
import logging
import os
from datetime import datetime
from platform.context import new_context, request_context
from platform.db_errors import APIError
from typing import Any

from langchain.tools import tool
from storage.database.supabase_client import get_supabase_client
from tools.tag_rules import validate_collection_tags, validate_memory_tags

logger = logging.getLogger(__name__)


def _get_client():
    """获取Supabase客户端"""
    ctx = request_context.get() or new_context(method="memory_tool")
    return get_supabase_client()


def _get_user_id() -> str:
    """从请求上下文获取当前用户ID，用于数据隔离"""
    ctx = request_context.get()
    if ctx and ctx.user_id:
        return ctx.user_id
    return os.getenv("COZE_USER_ID", "default")


def _parse_content(content: Any) -> str:
    """安全地将content转换为字符串"""
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        if content and isinstance(content[0], str):
            return " ".join(content)
        else:
            return " ".join(item.get("text", "") for item in content if isinstance(item, dict) and item.get("type") == "text")
    return str(content)


def _find_related_memories(client, user_id: str, exclude_id: int,
                           category: str | None = None,
                           mood: str | None = None,
                           topics: list[str] | None = None,
                           limit: int = 3) -> list[dict[str, Any]]:
    """搜索与当前记忆相关的历史记忆（按分类、情绪、主题匹配）"""
    related: list[dict[str, Any]] = []
    try:
        query = client.table("memories").select(
            "id, content, mood, location, topics, created_at, memory_categories(name)"
        ).eq("user_id", user_id).neq("id", exclude_id)

        if category:
            query = query.eq("memory_categories.name", category)

        response = query.order("created_at", desc=True).limit(limit * 3).execute()
        raw = response.data if isinstance(response.data, list) else []
        data: list[dict[str, Any]] = [d for d in raw if isinstance(d, dict)]

        # 优先匹配同主题的
        if topics:
            topic_set = set(topics)
            for d in data:
                if len(related) >= limit:
                    break
                d_topics = d.get("topics")
                if isinstance(d_topics, list) and (topic_set & set(d_topics)):
                    related.append(d)

        # 其次匹配同情绪的
        if mood and len(related) < limit:
            existing = {r["id"] for r in related}
            for d in data:
                if len(related) >= limit:
                    break
                if d.get("id") not in existing and d.get("mood") == mood:
                    related.append(d)

        # 不够则补充同分类的
        if len(related) < limit:
            existing = {r["id"] for r in related}
            for d in data:
                if len(related) >= limit:
                    break
                if d.get("id") not in existing:
                    related.append(d)

        # 格式化输出
        result = []
        for item in related[:limit]:
            cat_data = item.get("memory_categories")
            cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
            result.append({
                "id": item.get("id"),
                "content": item.get("content"),
                "mood": item.get("mood"),
                "topics": item.get("topics") or [],
                "category": cat_name,
                "location": item.get("location"),
                "created_at": item.get("created_at"),
            })
        return result
    except Exception as e:
        logger.warning(f"联想相关记忆失败: {e}")
        return []


def _find_related_collections(client, user_id: str, exclude_id: int,
                               topics: list[str] | None = None,
                               content_type: str | None = None,
                               limit: int = 3) -> list[dict[str, Any]]:
    """搜索与当前收藏相关的历史收藏（按主题、类型匹配）"""
    related: list[dict[str, Any]] = []
    try:
        query = client.table("knowledge_collections").select(
            "id, title, content_type, topics, created_at"
        ).eq("user_id", user_id).neq("id", exclude_id)

        if content_type:
            query = query.eq("content_type", content_type)

        response = query.order("created_at", desc=True).limit(limit * 3).execute()
        raw = response.data if isinstance(response.data, list) else []
        data: list[dict[str, Any]] = [d for d in raw if isinstance(d, dict)]

        # 优先匹配同主题的
        if topics:
            topic_set = set(topics)
            for d in data:
                if len(related) >= limit:
                    break
                d_topics = d.get("topics")
                if isinstance(d_topics, list) and (topic_set & set(d_topics)):
                    related.append(d)

        # 不够则补充同类型的
        if len(related) < limit:
            existing = {r["id"] for r in related}
            for d in data:
                if len(related) >= limit:
                    break
                if d.get("id") not in existing:
                    related.append(d)

        # 格式化输出
        result = []
        for item in related[:limit]:
            result.append({
                "id": item.get("id"),
                "title": item.get("title") or "无标题",
                "content_type": item.get("content_type"),
                "topics": item.get("topics") or [],
                "created_at": item.get("created_at"),
            })
        return result
    except Exception as e:
        logger.warning(f"联想相关收藏失败: {e}")
        return []


@tool
def save_memory(
    content: str,
    category: str | None = None,
    mood: str | None = None,
    topics: list[str] | None = None,
    location: str | None = None,
    people: list[str] | None = None,
    content_format: str | None = None
) -> str:
    """
    保存一条新的记忆到数据库中。

    Args:
        content: 记忆的原始内容（必填）
        category: 记忆分类（七选一）：情绪、事件、想法、灵感、人物、地点、待分类
        mood: 情绪状态（八选一）：开心、平静、感恩、期待、悲伤、焦虑、愤怒、疲惫
        topics: 主题领域（1~2个，十三选一）：学习、工作、健康、美食、情感、旅行、家庭、社交、兴趣、目标、生活、认知、其他
        location: 相关地点（可选）
        people: 相关人物列表（可选）
        content_format: 内容形式：text、image、voice、link，默认text

    Returns:
        保存结果的JSON字符串，包含记忆ID、标签信息和相关历史记忆
    """
    client = _get_client()

    # V3标签校验与归一化
    validated = validate_memory_tags(
        category=category, mood=mood, content_format=content_format,
    )

    # 构建记忆数据
    memory_data: dict[str, Any] = {
        "content": content,
        "mood": validated["mood"],
        "content_format": validated["content_format"],
        "user_id": _get_user_id(),
    }
    if location:
        memory_data["location"] = location
    if people:
        memory_data["people"] = people
    if topics:
        memory_data["topics"] = topics

    # 处理分类（归一化后的值）
    cat_name = validated["category"]
    if cat_name:
        try:
            cat_response = client.table("memory_categories").select("id").eq("name", cat_name).maybe_single().execute()
            if cat_response and cat_response.data:
                cat_data: dict[str, Any] = cat_response.data
                memory_data["category_id"] = cat_data["id"]
            else:
                new_cat = client.table("memory_categories").insert({"name": cat_name}).execute()
                if new_cat.data:
                    new_cat_list: list[dict[str, Any]] = new_cat.data
                    memory_data["category_id"] = new_cat_list[0]["id"]
        except APIError as e:
            logger.warning(f"处理分类时出错: {e.message}")

    try:
        response = client.table("memories").insert(memory_data).execute()
        if response.data:
            response_list: list[dict[str, Any]] = response.data
            memory_id = response_list[0]["id"]
            user_id = _get_user_id()

            # 自动联想相关历史记忆
            related = _find_related_memories(
                client, user_id, memory_id,
                category=validated["category"],
                mood=validated["mood"],
                topics=topics,
                limit=3
            )

            return json.dumps({
                "success": True,
                "memory_id": memory_id,
                "message": f"记忆已成功保存，ID: {memory_id}",
                "category": validated["category"],
                "mood": validated["mood"],
                "topics": topics or [],
                "people": people,
                "location": location,
                "related_memories": related,
                "_hint": "回复用户时展示：✅已保存、📝内容、🏷️分类、💭情绪、📌主题、👫同行(如有)、📍地点(如有)。如果有related_memories，自然地提及相关历史记忆，如「这让我想起你X月X日也记录过类似的事情～」。严禁展示内容形式(content_format)、memory_id。",
            }, ensure_ascii=False)
        else:
            return json.dumps({"success": False, "message": "保存失败，未返回数据"}, ensure_ascii=False)
    except APIError as e:
        logger.error(f"保存记忆失败: {e.message}")
        raise Exception(f"保存记忆失败: {e.message}")


@tool
def search_memories(
    query: str | None = None,
    category: str | None = None,
    mood: str | None = None,
    topics: list[str] | None = None,
    limit: int = 10
) -> str:
    """
    搜索历史记忆。支持按关键词、分类、情绪、主题等条件筛选。
    用户可能用模糊语言描述，如「我之前心情不好的时候记录了什么」→ mood=悲伤/焦虑；
    「上个月关于旅行的事情」→ topics=旅行；「我和朋友相关的内容」→ category=人物。

    Args:
        query: 搜索关键词（在内容中模糊匹配）
        category: 按分类筛选（情绪、事件、想法、灵感、人物、地点、待分类）
        mood: 按情绪状态筛选（开心、平静、感恩、期待、悲伤、焦虑、愤怒、疲惫）
        topics: 按主题筛选（学习、工作、健康、美食、情感、旅行、家庭、社交、兴趣、目标、生活、认知、其他）
        limit: 返回结果数量限制，默认10条

    Returns:
        搜索结果的JSON字符串，包含匹配的记忆列表
    """
    client = _get_client()

    try:
        # 构建查询（按 user_id 隔离）
        db_query = client.table("memories").select("id, content, summary, mood, topics, location, people, created_at, memory_categories(name)").eq("user_id", _get_user_id())

        # 应用过滤条件
        if category:
            db_query = db_query.eq("memory_categories.name", category)
        if mood:
            db_query = db_query.eq("mood", mood)
        if query:
            # 使用ilike进行模糊搜索
            db_query = db_query.ilike("content", f"%{query}%")

        # 按时间倒序，限制数量
        db_query = db_query.order("created_at", desc=True).limit(min(limit, 50))

        response = db_query.execute()

        memories: list[dict[str, Any]] = []
        response_data: list[dict[str, Any]] = response.data if response.data else []
        for mem in response_data:
            cat_data = mem.get("memory_categories")
            category_name = None
            if cat_data and isinstance(cat_data, dict):
                category_name = cat_data.get("name")

            memories.append({
                "id": mem.get("id"),
                "content": mem.get("content"),
                "summary": mem.get("summary"),
                "mood": mem.get("mood"),
                "topics": mem.get("topics") or [],
                "location": mem.get("location"),
                "people": mem.get("people") or [],
                "category": category_name,
                "created_at": mem.get("created_at")
            })

        # 如果指定了主题，在Python层过滤（JSON数组包含匹配）
        if topics:
            topic_set = set(topics)
            memories = [m for m in memories if isinstance(m.get("topics"), list) and topic_set & set(m["topics"])]

        return json.dumps({
            "success": True,
            "count": len(memories),
            "memories": memories
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"搜索记忆失败: {e.message}")
        raise Exception(f"搜索记忆失败: {e.message}")


@tool
def get_recent_memories(days: int = 7, limit: int = 20) -> str:
    """
    获取最近几天的记忆记录。

    Args:
        days: 获取最近多少天的记忆，默认7天
        limit: 返回结果数量限制，默认20条

    Returns:
        最近记忆的JSON字符串
    """
    client = _get_client()

    try:
        from datetime import timedelta
        cutoff_date = (datetime.utcnow() - timedelta(days=days)).isoformat()

        response = client.table("memories") \
            .select("id, content, summary, mood, topics, category_id, created_at, memory_categories(name)") \
            .eq("user_id", _get_user_id()) \
            .gte("created_at", cutoff_date) \
            .order("created_at", desc=True) \
            .limit(min(limit, 100)) \
            .execute()

        memories: list[dict[str, Any]] = []
        response_data: list[dict[str, Any]] = response.data if response.data else []
        for mem in response_data:
            cat_data = mem.get("memory_categories")
            cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
            memories.append({
                "id": mem.get("id"),
                "content": mem.get("content"),
                "summary": mem.get("summary"),
                "mood": mem.get("mood"),
                "topics": mem.get("topics") or [],
                "category": cat_name,
                "created_at": mem.get("created_at")
            })

        return json.dumps({
            "success": True,
            "period_days": days,
            "count": len(memories),
            "memories": memories
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"获取最近记忆失败: {e.message}")
        raise Exception(f"获取最近记忆失败: {e.message}")


@tool
def get_memory_stats() -> str:
    """
    获取记忆统计信息，包括总数、各分类数量、情绪分布等。

    Returns:
        统计信息的JSON字符串
    """
    client = _get_client()

    try:
        user_id = _get_user_id()
        # 获取总记忆数（按用户隔离）
        total_response = client.table("memories").select("id", count="exact").eq("user_id", user_id).execute()
        total_count = total_response.count if total_response.count else 0

        # 获取分类统计
        categories_response = client.table("memory_categories").select("id, name").execute()
        category_stats: list[dict[str, Any]] = []
        categories_data: list[dict[str, Any]] = categories_response.data if categories_response.data else []
        for cat in categories_data:
            cat_memories = client.table("memories").select("id", count="exact").eq("user_id", user_id).eq("category_id", cat.get("id")).execute()
            category_stats.append({
                "name": cat.get("name"),
                "count": cat_memories.count if cat_memories.count else 0
            })

        # 获取情绪分布（按用户隔离）
        mood_response = client.table("memories").select("mood").eq("user_id", user_id).execute()
        mood_counts: dict[str, int] = {}
        mood_data: list[dict[str, Any]] = mood_response.data if mood_response.data else []
        for mem in mood_data:
            mood = mem.get("mood") or "未标记"
            mood_counts[mood] = mood_counts.get(mood, 0) + 1

        # 获取主题分布
        topics_response = client.table("memories").select("topics").eq("user_id", user_id).execute()
        topic_counts: dict[str, int] = {}
        topics_data: list[dict[str, Any]] = topics_response.data if topics_response.data else []
        for mem in topics_data:
            mem_topics = mem.get("topics")
            if isinstance(mem_topics, list):
                for t in mem_topics:
                    topic_counts[t] = topic_counts.get(t, 0) + 1

        return json.dumps({
            "success": True,
            "total_memories": total_count,
            "categories": category_stats,
            "mood_distribution": mood_counts,
            "topic_distribution": topic_counts
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"获取统计信息失败: {e.message}")
        raise Exception(f"获取统计信息失败: {e.message}")


@tool
def update_memory_summary(memory_id: int, summary: str) -> str:
    """
    更新记忆的AI摘要。

    Args:
        memory_id: 记忆ID
        summary: AI生成的摘要内容

    Returns:
        更新结果的JSON字符串
    """
    client = _get_client()

    try:
        response = client.table("memories") \
            .update({"summary": summary}) \
            .eq("id", memory_id) \
            .eq("user_id", _get_user_id()) \
            .execute()

        if response.data:
            return json.dumps({
                "success": True,
                "message": f"记忆 {memory_id} 的摘要已更新"
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": f"未找到ID为 {memory_id} 的记忆"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"更新记忆摘要失败: {e.message}")
        raise Exception(f"更新记忆摘要失败: {e.message}")


@tool
def delete_memory(memory_id: int) -> str:
    """
    删除指定的记忆。

    Args:
        memory_id: 要删除的记忆ID

    Returns:
        删除结果的JSON字符串
    """
    client = _get_client()

    try:
        response = client.table("memories").delete().eq("id", memory_id).eq("user_id", _get_user_id()).execute()

        if response.data:
            return json.dumps({
                "success": True,
                "message": f"记忆 {memory_id} 已删除"
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": f"未找到ID为 {memory_id} 的记忆"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"删除记忆失败: {e.message}")
        raise Exception(f"删除记忆失败: {e.message}")


@tool
def save_knowledge_collection(
    content: str,
    content_type: str = "文章",
    title: str | None = None,
    author: str | None = None,
    source: str | None = None,
    topics: list[str] | None = None,
    image_url: str | None = None,
    mood: str | None = None,
    content_format: str | None = None
) -> str:
    """
    收藏一条外部信息到知识库。

    当用户分享外部信息（文章、帖子、观点、数据、图片等）时使用此工具。

    Args:
        content: 内容（必填）
        content_type: 收藏分类（六选一）：文章、帖子、观点、数据、图片、其他，默认文章
        title: 标题（可选）
        author: 作者（可选）
        source: 来源，如URL、平台名、出处（可选）
        topics: 主题领域（1~2个，十三选一）：学习、工作、健康、美食、情感、旅行、家庭、社交、兴趣、目标、生活、认知、其他
        image_url: 图片URL（可选）
        mood: 情绪状态（可选，八选一）：开心、平静、感恩、期待、悲伤、焦虑、愤怒、疲惫
        content_format: 内容形式：text、image、voice、link，默认text

    Returns:
        保存结果的JSON字符串
    """
    client = _get_client()

    # V3标签校验与归一化
    validated = validate_collection_tags(
        content_type=content_type, topics=topics,
        mood=mood, content_format=content_format,
    )

    try:
        data: dict[str, Any] = {
            "content": content,
            "content_type": validated["content_type"],
            "content_format": validated["content_format"],
            "user_id": _get_user_id(),
        }

        if title:
            data["title"] = title
        if author:
            data["author"] = author
        if source:
            data["source"] = source
        if validated["topics"]:
            data["topics"] = validated["topics"]
        if validated["mood"]:
            data["mood"] = validated["mood"]
        if image_url:
            data["image_url"] = image_url

        response = client.table("knowledge_collections").insert(data).execute()

        if response.data:
            item = response.data[0] if isinstance(response.data, list) else response.data
            item_id = item.get("id") if isinstance(item, dict) else None
            user_id = _get_user_id()

            # 自动联想相关历史收藏
            related_cols = _find_related_collections(
                client, user_id, item_id,
                topics=validated["topics"],
                content_type=validated["content_type"],
                limit=3
            )

            # 同时联想相关个人记忆（按主题匹配）
            related_mems: list[dict[str, Any]] = []
            if validated["topics"]:
                try:
                    mem_resp = client.table("memories").select(
                        "id, content, mood, topics, created_at, memory_categories(name)"
                    ).eq("user_id", user_id).order("created_at", desc=True).limit(20).execute()
                    raw_data = mem_resp.data if isinstance(mem_resp.data, list) else []
                    topic_set = set(validated["topics"])
                    related_mems: list[dict[str, Any]] = []
                    for m in raw_data:
                        if not isinstance(m, dict):
                            continue
                        m_topics = m.get("topics")
                        if not isinstance(m_topics, list):
                            continue
                        if not (topic_set & set(m_topics)):
                            continue
                        cat_data = m.get("memory_categories")
                        cat_name = cat_data.get("name") if isinstance(cat_data, dict) else None
                        related_mems.append({
                            "id": m.get("id"),
                            "content": m.get("content"),
                            "mood": m.get("mood"),
                            "topics": m_topics,
                            "category": cat_name,
                            "created_at": m.get("created_at"),
                        })
                        if len(related_mems) >= 3:
                            break
                except Exception as e:
                    logger.warning(f"联想相关记忆失败: {e}")

            return json.dumps({
                "success": True,
                "collection_id": item_id,
                "message": f"知识收藏已成功保存，ID: {item_id}",
                "content_type": validated["content_type"],
                "title": title,
                "topics": validated["topics"],
                "mood": validated["mood"],
                "related_collections": related_cols,
                "related_memories": related_mems,
                "_hint": "回复用户时展示：✅已收藏、📝标题、🏷️类型、📌主题、💭情绪(如有)。如果有related_memories或related_collections，自然地提及相关历史内容。严禁展示content_format、collection_id。",
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": "保存失败，未返回数据"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"保存知识收藏失败: {e.message}")
        raise Exception(f"保存知识收藏失败: {e.message}")


@tool
def search_knowledge_collections(
    query: str | None = None,
    content_type: str | None = None,
    topics: list[str] | None = None,
    limit: int = 10
) -> str:
    """
    搜索知识库中的收藏内容。

    Args:
        query: 搜索关键词（可选，在内容中模糊匹配）
        content_type: 内容类型筛选（可选）
        topics: 主题筛选（可选）
        limit: 返回数量，默认10

    Returns:
        搜索结果的JSON字符串
    """
    client = _get_client()

    try:
        query_builder = client.table("knowledge_collections").select("*").eq("user_id", _get_user_id())

        if query:
            query_builder = query_builder.ilike("content", f"%{query}%")
        if content_type:
            query_builder = query_builder.eq("content_type", content_type)

        response = query_builder.order("created_at", desc=True).limit(limit).execute()

        data: list[dict[str, Any]] = response.data if response.data else []

        # 过滤topics（客户端过滤）
        filtered_data = data
        if topics:
            filtered_data = [
                item for item in filtered_data
                if isinstance(item.get("topics"), list) and any(t in item.get("topics", []) for t in topics)
            ]

        return json.dumps({
            "success": True,
            "count": len(filtered_data),
            "collections": filtered_data
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"搜索知识收藏失败: {e.message}")
        raise Exception(f"搜索知识收藏失败: {e.message}")


@tool
def get_knowledge_summary(topics: list[str] | None = None, days: int = 7) -> str:
    """
    获取知识库的总结，包括收藏数量、主题分布等。

    Args:
        topics: 指定主题（可选，不指定则总结所有）
        days: 最近多少天，默认7天

    Returns:
        总结信息的JSON字符串
    """
    client = _get_client()

    try:
        from datetime import timedelta
        cutoff_date = datetime.now() - timedelta(days=days)

        query_builder = client.table("knowledge_collections").select("*").eq("user_id", _get_user_id()).gte("created_at", cutoff_date.isoformat())

        if topics:
            response = query_builder.execute()
            data: list[dict[str, Any]] = response.data if response.data else []
            data = [
                item for item in data
                if isinstance(item.get("topics"), list) and any(t in item.get("topics", []) for t in topics)
            ]
        else:
            response = query_builder.execute()
            data: list[dict[str, Any]] = response.data if response.data else []

        # 统计
        total_count = len(data)
        type_counts: dict[str, int] = {}
        topic_counts: dict[str, int] = {}

        for item in data:
            # 类型统计
            ctype = item.get("content_type", "other")
            type_counts[ctype] = type_counts.get(ctype, 0) + 1

            # 主题统计
            item_topics = item.get("topics", [])
            if isinstance(item_topics, list):
                for t in item_topics:
                    topic_counts[t] = topic_counts.get(t, 0) + 1

        return json.dumps({
            "success": True,
            "period_days": days,
            "total_collections": total_count,
            "type_distribution": type_counts,
            "topic_distribution": topic_counts
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"获取知识总结失败: {e.message}")
        raise Exception(f"获取知识总结失败: {e.message}")
