"""
忆光 - 链接内容提取与知识管理工具
提供链接内容提取、删除收藏、导出收藏等功能
"""
import json
import logging
import os
from typing import Any

from langchain.tools import tool
from storage.database.supabase_client import get_supabase_client
from yishu.context import new_context, request_context
from yishu.db_errors import APIError

logger = logging.getLogger(__name__)


def _get_client():
    """获取Supabase客户端"""
    return get_supabase_client()


def _export_unavailable_reason() -> str:
    """导出功能是否可用；**空串表示可用**，否则返回不可用原因（2026-10-05 新增）。

    ## 为什么必须在入口预检

    `export_knowledge_collections` 依赖 Coze 平台的两样东西：
      · 私有包 `coze_coding_dev_sdk`（提供 `S3SyncStorage`）
      · 环境变量 `COZE_BUCKET_ENDPOINT_URL` / `COZE_BUCKET_NAME`

    去 Coze 改造后这两样在本项目**都不存在** ⇒ 这个工具**必然失败**。
    而它同时满足两个"会被调用"的条件：
      · 挂在 `build_agent` 的工具列表里；
      · 被 system prompt 明确宣传（"用户需要导出收藏时使用"）。
    于是用户一说"导出我的收藏"，LLM 就会调它、拿到一个含内部模块路径的报错、
    然后**可能反复重试** —— 这正是 save_memory 曾经单轮 32~66s 的同一根因族：
    **一个静默/必然失败的工具挂在列表里 = 延迟炸弹**。

    预检把"必然失败"提前成"立刻给出可读结论"，不让用户干等一整轮 agent 循环。
    预检成本是一次 import 尝试 + 两次字典查，可忽略。

    ## 判定顺序

    先查依赖再查配置：缺依赖时即便配了凭据也用不了（配置是必要条件而非充分条件），
    报"缺依赖"对运维更有指向性。
    """
    try:
        import coze_coding_dev_sdk.s3  # noqa: F401 —— 仅探测可用性
    except ImportError:
        # ⚠️ 2026-10-06：**不要把内部包名写进 reason**。reason 会拼进返回体，
        # 返回体会作为 ToolMessage 进 LLM 上下文——把私有包名喂给模型
        # 毫无价值且属于内部信息外泄。给运维看的细节走日志（见调用点 log_event）。
        return "导出所需的存储依赖未安装"
    if not os.getenv("COZE_BUCKET_ENDPOINT_URL") or not os.getenv("COZE_BUCKET_NAME"):
        return "未配置对象存储（COZE_BUCKET_ENDPOINT_URL / COZE_BUCKET_NAME）"
    return ""


def _get_user_id() -> str:
    """从请求上下文取当前 user_id（**fail-closed**，2026-10-04）。

    与 `tools/memory_tools._get_user_id` **必须同口径**：原实现同样回落到
    `os.getenv("COZE_USER_ID", "default")` 这个全局共享身份，一旦上下文缺失，
    `delete_knowledge_collection` / `export_knowledge_collections` /
    `link_memory_to_collection` 会作用在**所有人的**收藏上（跨用户串档），
    而且不报错。两个文件必须一起改，否则修一边等于没修。
    """
    ctx = request_context.get()
    if ctx and ctx.user_id:
        return ctx.user_id
    from yishu.observability import log_audit

    log_audit("user_id_missing_reject", tool="url_fetch_tools")
    raise PermissionError("缺少 user_id，拒绝执行收藏操作（防止跨用户串档）")


def _init_fetch_client():
    """初始化链接内容提取客户端。

    去 Coze（2026-09-23 · AG4）：原 `coze_coding_dev_sdk.fetch.FetchClient`（平台代理抓取）
    换为本地适配器 `yishu/fetch.py`（httpx + bs4，**结果契约照上游逐字段对齐**，
    另加 SSRF 防护与体积/超时上限）。`ctx` 参数保留以兼容调用点。
    """
    from yishu.fetch import FetchClient

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
        # 2026-10-14（台账 §7.21 · 批次 8 审计）：**回给模型的 message 不带异常原文**。
        # 原样带 `str(e)` 会把底层库/路径细节（乃至配置片段）送进模型上下文；同仓已按
        # `type(exc).__name__` 加固（agents/agent.py:103-112、本文件 :317-323、
        # storage/local_client.py:429-462），此处是漏网的一处 ⇒ 只回异常类型名，
        # 细节保留在服务端日志（上面那行 logger.error 已带全量）。
        return json.dumps({
            "success": False,
            "message": f"链接内容提取失败（{type(e).__name__}）",
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
        response = client.table("knowledge_collections").delete().eq(
            "id", collection_id
        ).eq("user_id", _get_user_id()).execute()

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
        raise Exception(f"删除知识收藏失败: {e.message}") from e


@tool
def export_knowledge_collections() -> str:
    """
    将用户的所有知识收藏导出为 Markdown 文件，并上传到对象存储。

    当用户要求导出、备份收藏内容时使用此工具。
    生成的文件会返回一个可下载的链接。

    Returns:
        导出结果的JSON字符串，包含下载链接
    """
    # 入口预检（2026-10-05）：依赖缺失时**立刻**返回可读结论，不走后续 DB 查询与
    # 一轮 agent 循环。返回 JSON（而不是抛异常）是刻意的——抛异常会被包装成
    # ToolMessage 喂回 LLM，模型倾向于"再试一次"从而放大延迟；返回一个明确的
    # success=False 能让它直接如实转述给用户。详见 `_export_unavailable_reason`。
    unavailable = _export_unavailable_reason()
    if unavailable:
        from yishu.observability import log_event

        # 给运维的细节（具体缺哪个包/哪个变量）走日志；给模型/用户的 reason 是脱敏后的。
        logger.warning("导出工具不可用: %s", unavailable)
        log_event(
            "tool_unavailable",
            level=logging.WARNING,
            tool="export_knowledge_collections",
            reason=unavailable,
        )
        return json.dumps({
            "success": False,
            "unavailable": True,
            "message": (
                f"导出功能当前不可用（{unavailable}）。"
                "请如实告知用户：收藏内容仍完整保存在知识库中、可随时在应用内查看，"
                "暂时不支持导出为文件；不要说成'导出失败'，也不要重试本工具。"
            ),
        }, ensure_ascii=False)

    client = _get_client()

    try:
        response = client.table("knowledge_collections").select("*").eq(
            "user_id", _get_user_id()
        ).order("created_at", desc=True).execute()
        collection_data: list[dict[str, Any]] = response.data if response.data else []

        if not collection_data:
            return json.dumps({
                "success": False,
                "message": "当前没有可导出的收藏内容"
            }, ensure_ascii=False)

        # 生成 Markdown 内容
        lines: list[str] = ["# 我的知识收藏", ""]
        # 导出时间戳（本地时区可读性优先，仅用于文件名/页眉，不参与任何时序判定）
        exported_at = __import__('datetime').datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines.append(f"共 {len(collection_data)} 条收藏，导出时间：{exported_at}")
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
        raise Exception(f"导出知识收藏失败: {e.message}") from e
    except Exception as e:
        # ⚠️ 2026-10-05 安全：原先 `raise Exception(f"...{str(e)}")` —— 这个 message
        # 会被 LangChain 包成 ToolMessage **进 LLM 上下文**，模型可能复述给用户，
        # 而 str(e) 里可能有 DSN、绝对路径、内部模块名。只保留异常类型名：
        # 足够让模型区分"配置问题/权限问题/网络问题"，又不带内部信息。
        # 细节已由上面一行 logger.error 完整落盘，日志侧不丢信息。
        logger.error("导出知识收藏失败: %s", e)
        raise Exception(f"导出知识收藏失败（{type(e).__name__}），请稍后重试") from e


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
        response = client.table("knowledge_collections").select("related_memory_ids").eq(
            "id", collection_id
        ).eq("user_id", user_id).execute()

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
        raise Exception(f"关联记忆失败: {e.message}") from e
