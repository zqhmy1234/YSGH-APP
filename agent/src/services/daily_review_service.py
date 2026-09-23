"""
定时复盘任务服务
每天晚上 22:00 生成今日记忆回顾，通过 WebSocket 主动推送到用户的一对一对话
"""
import os
import json
import asyncio
import logging
from datetime import datetime
from typing import Any

from supabase import Client

from storage.database.supabase_client import get_supabase_client
from coze_coding_utils.log.write_log import request_context
from coze_coding_utils.runtime_ctx.context import new_context

logger = logging.getLogger(__name__)

# 复盘推送时间配置（默认每天晚上 22:00）
REVIEW_HOUR = int(os.getenv("REVIEW_HOUR", "22"))
REVIEW_MINUTE = int(os.getenv("REVIEW_MINUTE", "0"))

# V3 情绪 emoji 映射
MOOD_EMOJI = {
    "开心": "😊",
    "平静": "😌",
    "感恩": "🙏",
    "期待": "🌟",
    "悲伤": "😢",
    "焦虑": "😰",
    "愤怒": "😠",
    "疲惫": "😮‍💨",
}


def _get_db() -> Client:
    return get_supabase_client()


async def _get_active_users() -> list[dict[str, Any]]:
    """获取所有开启了复盘推送的用户"""
    db = _get_db()
    result = db.table("wechat_users").select("user_id, chat_id, user_name").eq("review_enabled", True).execute()
    return result.data if result.data else []


async def _get_today_memories(user_id: str) -> list[dict[str, Any]]:
    """获取用户今天的所有记忆"""
    db = _get_db()
    today = datetime.now().strftime("%Y-%m-%d")
    result = (
        db.table("memories")
        .select("content, category, mood, topics, location, created_at, content_format")
        .eq("user_id", user_id)
        .gte("created_at", f"{today}T00:00:00+08:00")
        .lte("created_at", f"{today}T23:59:59+08:00")
        .order("created_at", desc=False)
        .execute()
    )
    return result.data if result.data else []


def _generate_review_content(user_name: str, memories: list[dict[str, Any]]) -> str:
    """生成今日回顾内容（Markdown 格式）"""
    today = datetime.now().strftime("%Y年%m月%d日")
    parts = [f"📊 忆光 · 今日回顾\n{today}\n"]

    # 统计
    parts.append(f"今天共记录了 {len(memories)} 条记忆\n")

    # 情绪分布
    mood_counts: dict[str, int] = {}
    for mem in memories:
        mood = mem.get("mood") or "平静"
        mood_counts[mood] = mood_counts.get(mood, 0) + 1

    if mood_counts:
        mood_str = " | ".join(
            f"{MOOD_EMOJI.get(m, '💭')} {m} {c}条" for m, c in mood_counts.items()
        )
        parts.append(f"💭 情绪分布：{mood_str}\n")

    # 分类分布
    cat_counts: dict[str, int] = {}
    for mem in memories:
        cat = mem.get("category") or "待分类"
        cat_counts[cat] = cat_counts.get(cat, 0) + 1

    if cat_counts:
        cat_str = " | ".join(f"{c}条 {cat}" for cat, c in cat_counts.items())
        parts.append(f"🏷️ 分类：{cat_str}\n")

    # 主题分布
    topic_counts: dict[str, int] = {}
    for mem in memories:
        topics = mem.get("topics")
        if isinstance(topics, list):
            for t in topics:
                topic_counts[t] = topic_counts.get(t, 0) + 1

    if topic_counts:
        topic_str = " | ".join(f"{t}({c})" for t, c in sorted(topic_counts.items(), key=lambda x: -x[1]))
        parts.append(f"📌 主题：{topic_str}\n")

    # 记忆列表
    parts.append("—" * 20)
    for i, mem in enumerate(memories, 1):
        content = (mem.get("content") or "")[:40]
        mood = mem.get("mood") or ""
        mood_icon = MOOD_EMOJI.get(mood, "💭")
        fmt = mem.get("content_format") or "text"
        fmt_icon = "🎤" if fmt == "voice" else ("🔗" if fmt == "link" else "📝")
        location = mem.get("location") or ""
        loc_str = f" 📍{location}" if location else ""
        parts.append(f"{i}. {fmt_icon} {content}… {mood_icon}{loc_str}")

    # 结尾
    parts.append("—" * 20)
    parts.append("每一段记忆都是生活的珍贵碎片 💫 晚安~")

    return "\n".join(parts)


async def send_daily_review_to_user(user_id: str, chat_id: str, user_name: str):
    """为单个用户生成并发送今日回顾"""
    from services.wechat_service import get_service

    service = get_service()
    if service is None or service.client is None:
        logger.error("WSClient 未就绪，无法发送回顾")
        return

    try:
        memories = await _get_today_memories(user_id)

        if not memories:
            logger.info(f"用户 {user_id} 今天没有记忆记录，跳过回顾")
            return

        review_text = _generate_review_content(user_name or "你", memories)

        # 通过 WebSocket 主动推送到用户的一对一对话
        await service.client.send_message(
            chat_id,
            {"msgtype": "markdown", "markdown": {"content": review_text}}
        )
        logger.info(f"今日回顾已推送到用户 {user_id}")

    except Exception as e:
        logger.error(f"为用户 {user_id} 发送回顾失败: {e}", exc_info=True)


async def send_daily_review():
    """发送今日回顾到所有开启了推送的用户"""
    logger.info(f"开始执行每日回顾任务 (计划时间: {REVIEW_HOUR:02d}:{REVIEW_MINUTE:02d})")

    try:
        users = await _get_active_users()

        if not users:
            logger.info("没有需要推送回顾的用户")
            return

        logger.info(f"共 {len(users)} 位用户需要推送回顾")

        for user in users:
            user_id = user.get("user_id")
            chat_id = user.get("chat_id") or user_id
            user_name = user.get("user_name") or ""

            if not user_id:
                continue

            await send_daily_review_to_user(user_id, chat_id, user_name)

        logger.info("每日回顾任务完成")

    except Exception as e:
        logger.error(f"每日回顾任务失败: {e}", exc_info=True)


async def start_review_scheduler():
    """启动复盘定时任务"""
    logger.info(f"回顾定时任务已启动，每天 {REVIEW_HOUR:02d}:{REVIEW_MINUTE:02d} 执行")

    while True:
        now = datetime.now()

        # 检查是否到达执行时间
        if now.hour == REVIEW_HOUR and now.minute == REVIEW_MINUTE:
            await send_daily_review()
            # 等待1分钟，避免重复执行
            await asyncio.sleep(60)

        # 每30秒检查一次
        await asyncio.sleep(30)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(start_review_scheduler())
