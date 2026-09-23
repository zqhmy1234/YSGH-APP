"""
忆光 - 链接内容提取与知识管理工具
提供链接内容提取、删除收藏、导出收藏等功能
"""
import json
import logging
import os
from platform.context import new_context, request_context
from platform.db_errors import APIError
from typing import Any

from langchain.tools import tool
from storage.database.supabase_client import get_supabase_client

logger = logging.getLogger(__name__)


def _get_client():
    """获取Supabase客户端"""
    return get_supabase_client()


def _get_user_id() -> str:
    """从请求上下文获取当前用户ID，用于数据隔离"""
    ctx = request_context.get()
    if ctx and ctx.user_id:
        return ctx.user_id
    return os.getenv("COZE_USER_ID", "default")


def _init_fetch_client():
    """初始化链接内容提取客户端。

    去 Coze（2026-09-23 · AG4）：原 `coze_coding_dev_sdk.fetch.FetchClient`（平台代理抓取）
    换为本地适配器 `platform/fetch.py`（httpx + bs4，**结果契约照上游逐字段对齐**，
    另加 SSRF 防护与体积/超时上限）。`ctx` 参数保留以兼容调用点。
    """
    from platform.fetch import FetchClient

    ctx = request_context.get() or new_context(method="fetch_url_content")
    return FetchClient(ctx=ctx)


@tool
def fetch_url_content(url: str) -> str:
    """
    从链接提取网页内容，包括标题、正文、图片等。

    当用户分享链接（公众号文章、网页文章、帖子等）时使用此工具，
    自动提取内容供后续摘要、分类和保存。

    Args:
        url: 要提取内容的链接地址

    Returns:
        提取到的内容的JSON字符串，包含标题、正文、图片链接等
    """
    try:
        client = _init_fetch_client()
        response = client.fetch(url=url)

        if response.status_code != 0:
            return json.dumps({
                "success": False,
                "message": f"链接提取失败: {response.status_message}",
                "url": url
            }, ensure_ascii=False)

        # 提取文本内容
        text_parts: list[str] = []
        images: list[dict[str, Any]] = []
        links: list[str] = []

        for item in response.content:
            if item.type == "text":
                text_parts.append(item.text)
            elif item.type == "image":
                images.append({
                    "image_url": item.image.image_url,
                    "display_url": item.image.display_url,
                    "width": item.image.width,
                    "height": item.image.height
                })
            elif item.type == "link":
                links.append(item.url)

        full_text = "\n".join(text_parts)

        return json.dumps({
            "success": True,
            "url": response.url or url,
            "title": response.title,
            "publish_time": getattr(response, "publish_time", None),
            "filetype": getattr(response, "filetype", None),
            "text_content": full_text,
            "text_length": len(full_text),
            "images": images,
            "images_count": len(images),
            "links": links
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"链接内容提取失败: {e}")
        return json.dumps({
            "success": False,
            "message": f"链接内容提取失败: {str(e)}",
            "url": url
        }, ensure_ascii=False)


@tool
def delete_knowledge_collection(collection_id: int) -> str:
    """
    删除指定的一条知识收藏。

    当用户要求删除某条收藏内容时使用此工具。

    Args:
        collection_id: 要删除的收藏ID

    Returns:
        删除结果的JSON字符串
    """
    client = _get_client()

    try:
        response = client.table("knowledge_collections").delete().eq("id", collection_id).eq("user_id", _get_user_id()).execute()

        if response.data:
            return json.dumps({
                "success": True,
                "message": f"收藏 {collection_id} 已删除"
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": f"未找到ID为 {collection_id} 的收藏"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"删除知识收藏失败: {e.message}")
        raise Exception(f"删除知识收藏失败: {e.message}")


@tool
def export_knowledge_collections() -> str:
    """
    将用户的所有知识收藏导出为 Markdown 文件，并上传到对象存储。

    当用户要求导出、备份收藏内容时使用此工具。
    生成的文件会返回一个可下载的链接。

    Returns:
        导出结果的JSON字符串，包含下载链接
    """
    client = _get_client()

    try:
        response = client.table("knowledge_collections").select("*").eq("user_id", _get_user_id()).order("created_at", desc=True).execute()
        collection_data: list[dict[str, Any]] = response.data if response.data else []

        if not collection_data:
            return json.dumps({
                "success": False,
                "message": "当前没有可导出的收藏内容"
            }, ensure_ascii=False)

        # 生成 Markdown 内容
        lines: list[str] = ["# 我的知识收藏", ""]
        lines.append(f"共 {len(collection_data)} 条收藏，导出时间：{__import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append("")

        for idx, item in enumerate(collection_data, 1):
            title = item.get("title") or "无标题"
            author = item.get("author")
            source = item.get("source")
            content_type = item.get("content_type")
            topics = item.get("topics") or []
            content = item.get("content") or ""
            summary = item.get("summary") or ""
            created_at = item.get("created_at")

            lines.append(f"## {idx}. {title}")
            lines.append("")
            lines.append(f"- **类型**: {content_type}")
            if author:
                lines.append(f"- **作者**: {author}")
            if source:
                lines.append(f"- **来源**: {source}")
            if topics:
                lines.append(f"- **主题**: {', '.join(topics)}")
            if created_at:
                lines.append(f"- **收藏时间**: {created_at}")
            lines.append("")

            if summary:
                lines.append(f"**摘要**: {summary}")
                lines.append("")
            if content:
                lines.append(f"**内容**: {content}")
                lines.append("")
            lines.append("---")
            lines.append("")

        markdown_content = "\n".join(lines)

        # 上传到对象存储
        import os

        from coze_coding_dev_sdk.s3 import S3SyncStorage

        storage = S3SyncStorage(
            endpoint_url=os.getenv("COZE_BUCKET_ENDPOINT_URL"),
            access_key="",
            secret_key="",
            bucket_name=os.getenv("COZE_BUCKET_NAME"),
            region="cn-beijing",
        )

        timestamp = __import__('datetime').datetime.now().strftime("%Y%m%d_%H%M%S")
        file_key = storage.upload_file(
            file_content=markdown_content.encode("utf-8"),
            file_name=f"knowledge_export_{timestamp}.md",
            content_type="text/markdown",
        )

        # 生成下载链接
        download_url = storage.generate_presigned_url(
            key=file_key,
            expire_time=86400,
        )

        return json.dumps({
            "success": True,
            "total_collections": len(collection_data),
            "download_url": download_url,
            "message": f"已导出 {len(collection_data)} 条收藏，下载链接有效期为24小时"
        }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"导出知识收藏失败: {e.message}")
        raise Exception(f"导出知识收藏失败: {e.message}")
    except Exception as e:
        logger.error(f"导出知识收藏失败: {e}")
        raise Exception(f"导出知识收藏失败: {str(e)}")


@tool
def link_memory_to_collection(collection_id: int, memory_id: int) -> str:
    """
    将一条知识收藏与一条个人记忆建立关联。

    当用户要求将某篇收藏的内容与之前的某个想法、经历关联时使用此工具。
    例如：用户说"把这篇冥想文章和我之前'想学习冥想'的想法关联起来"。

    Args:
        collection_id: 知识收藏ID
        memory_id: 个人记忆ID

    Returns:
        关联结果的JSON字符串
    """
    client = _get_client()

    try:
        user_id = _get_user_id()
        # 先读取当前收藏的关联记忆列表（按用户隔离）
        response = client.table("knowledge_collections").select("related_memory_ids").eq("id", collection_id).eq("user_id", user_id).execute()

        if not response.data:
            return json.dumps({
                "success": False,
                "message": f"未找到ID为 {collection_id} 的收藏"
            }, ensure_ascii=False)

        current_ids: list[int] = []
        item = response.data[0] if isinstance(response.data, list) else response.data
        existing = item.get("related_memory_ids") if isinstance(item, dict) else None
        if isinstance(existing, list):
            current_ids = [int(x) for x in existing]

        if memory_id not in current_ids:
            current_ids.append(memory_id)

        update_response = client.table("knowledge_collections") \
            .update({"related_memory_ids": current_ids}) \
            .eq("id", collection_id) \
            .eq("user_id", user_id) \
            .execute()

        if update_response.data:
            return json.dumps({
                "success": True,
                "message": f"收藏 {collection_id} 已关联记忆 {memory_id}",
                "related_memory_ids": current_ids
            }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "message": "关联失败"
            }, ensure_ascii=False)

    except APIError as e:
        logger.error(f"关联记忆失败: {e.message}")
        raise Exception(f"关联记忆失败: {e.message}")
