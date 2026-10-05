"""
企业微信智能机器人消息接收服务（WebSocket长连接模式）
基于 wecom_aibot_sdk 的 WSClient 事件驱动模型
"""
import asyncio
import logging
import os
from datetime import datetime
from yishu.context import new_context, request_context
from typing import Any

from agents.agent import build_agent
from storage.database.supabase_client import get_supabase_client
# ⚠️ 2026-10-06：原 `from supabase import Client`。`supabase` 从未在本包声明，
# 它只是 `coze-coding-dev-sdk` 的**传递依赖**（uv.lock:2456）——删掉 coze 包时
# 它会一并消失，这条 import 就成了第二个 ImportError（实测会让 `main_wechat.py`
# 进程起不来）。且 `get_supabase_client()` 早已返回 `LocalClient`，类型标注跟着
# 换到本地数据层才是真实的形状。
from storage.database.local_client import LocalClient
# ⚠️ 2026-10-06：删除了 `from tools.voice_tools import transcribe_voice`。
# 该函数依赖 `coze_coding_dev_sdk.ASRClient`（Coze 私有包，本项目**装不上**——
# 它的依赖链含 pycairo/dbus-python/PyGObject 三个 Linux-only 库，Windows 上
# `uv sync` 整体失败），是 agent 里最后一个 Coze 运行时 import。
#
# **更重要的理由**：语音转写在本项目**早有承载方**——设计（B5a）指定的
# FunASR/SenseVoice 双通道已落在 `backend/app/services/external/asr/`，
# App 侧走 `POST /api/v1/asr/transcribe`（`client/utils/voice.uts`）。
# agent 这条 ASR 是**第三套**、单通道、无情绪、无 VAD，且 `main_wechat.py`
# 未出现在任何 deploy/启动脚本里（不在生产链路上）。
# 让一个未接线的进程继续 import 一个装不上的包，只会让"整条企微链起不来"
# 这种硬故障披着 ImportError 的外衣。
from wecom_aibot_sdk import WSClient

logger = logging.getLogger(__name__)

# 企业微信智能机器人配置 (WSClient 只需 bot_id + secret)
BOT_ID = os.getenv("WECHAT_BOT_ID", "")
BOT_SECRET = os.getenv("WECHAT_BOT_SECRET", "") or os.getenv("WECHAT_CORP_SECRET", "")

# 全局单例
_service_instance: "WeChatMemoryService | None" = None


def get_service() -> "WeChatMemoryService | None":
    """获取全局服务实例（供 daily_review_service 等外部模块访问 WSClient）"""
    return _service_instance


class WeChatMemoryService:
    """企业微信消息服务 - 忆光记忆助手"""

    def __init__(self):
        self.agent = None
        self.client: WSClient | None = None
        self._db: LocalClient | None = None

    @property
    def db(self) -> LocalClient:
        if self._db is None:
            self._db = get_supabase_client()
        return self._db

    def _init_agent(self):
        """初始化Agent"""
        try:
            self.agent = build_agent()
            logger.info("Agent初始化成功")
        except Exception as e:
            logger.error(f"Agent初始化失败: {e}")
            self.agent = None

    async def start(self):
        """启动 WebSocket 长连接服务"""
        if not BOT_ID or not BOT_SECRET:
            logger.error("企业微信配置不完整: 需要 WECHAT_BOT_ID 和 WECHAT_BOT_SECRET")
            return

        self._init_agent()

        # 创建 WSClient 实例
        self.client = WSClient(bot_id=BOT_ID, secret=BOT_SECRET)

        # 注册事件处理器
        self.client.on("message.text", self._on_text)
        self.client.on("message.voice", self._on_voice)
        self.client.on("message.image", self._on_image)
        self.client.on("event.enter_chat", self._on_enter_chat)

        logger.info("正在建立WebSocket长连接...")
        await self.client.connect()

    async def _record_user(self, frame: dict[str, Any]):
        """记录/更新用户信息到 wechat_users 表（用于定时复盘推送）"""
        body = frame.get("body") or {}
        from_info = body.get("from") or {}
        user_id = from_info.get("user_id") or ""
        user_name = from_info.get("name") or ""
        chat_id = body.get("chatid") or user_id  # 单聊用 user_id，群聊用 chatid

        if not user_id or user_id == "default_user":
            return

        try:
            existing = self.db.table("wechat_users").select("id").eq("user_id", user_id).execute()
            now = datetime.now().isoformat()

            if existing.data:
                # 更新最后交互时间
                self.db.table("wechat_users").update({
                    "last_interaction_at": now,
                    "chat_id": chat_id,
                    "user_name": user_name or None,
                }).eq("user_id", user_id).execute()
            else:
                # 首次记录
                self.db.table("wechat_users").insert({
                    "user_id": user_id,
                    "chat_id": chat_id,
                    "user_name": user_name or None,
                    "first_interaction_at": now,
                    "last_interaction_at": now,
                    "review_enabled": True,
                }).execute()
                logger.info(f"新用户已记录: {user_id} ({user_name})")

        except Exception as e:
            # ⚠️ 2026-10-06：升级为**结构化事件**，并点破真实原因。
            # 此前这里只有一行 `logger.warning`，而实际根因是
            # `wechat_users` 这张表在本仓**根本不存在**（alembic 迁移与
            # `schema.sql` 都没有，连库 `to_regclass` 返回 NULL，实测确认）
            # ⇒ 用户的企微身份**永远静默落不了库**，后续"每日回顾推送给谁"
            # 也就永远查不到人。一行 warning 会让这被当成偶发网络抖动。
            from yishu.observability import log_event

            log_event(
                "wechat_record_user_failed",
                level=logging.ERROR,
                exc_type=type(e).__name__,
                detail=str(e)[:150],
                hint="wechat_users 表不存在（agent 无建表迁移）或 local_client 白名单未收录",
            )
            logger.error("记录用户信息失败: %s", e)

    async def _on_text(self, frame: dict[str, Any]):
        """处理文本消息"""
        body = frame.get("body") or {}
        content = (body.get("text") or {}).get("content", "")
        user_id = self._get_user_id(frame)

        logger.info(f"收到文本消息: {content[:50]}... from {user_id}")

        await self._record_user(frame)

        try:
            reply = await self._process_message(content, user_id)
            await self._reply_text(frame, reply)
        except Exception as e:
            logger.error(f"处理文本消息失败: {e}")
            await self._reply_text(frame, "抱歉，处理消息时遇到了一些问题，请稍后再试 😊")

    async def _on_voice(self, frame: dict[str, Any]):
        """
        处理语音消息 - 下载语音 → ASR转文字 → Agent处理 → 回复
        """
        body = frame.get("body") or {}
        voice_info = body.get("voice") or {}
        user_id = self._get_user_id(frame)

        logger.info(f"收到语音消息 from {user_id}")

        await self._record_user(frame)

        # 提取下载 URL 和 AES 密钥
        file_url = voice_info.get("file_url") or voice_info.get("media_id")
        aes_key = voice_info.get("aeskey") or voice_info.get("aes_key")

        if not file_url:
            logger.error(f"语音消息缺少 file_url, voice_info keys: {list(voice_info.keys())}")
            await self._reply_text(frame, "无法获取语音文件，请重新发送语音 🎤")
            return

        try:
            # 1. 下载并解密语音文件
            download_result = await self.client.download_file(file_url, aes_key)
            audio_bytes: bytes = download_result.get("buffer", b"")
            filename = download_result.get("filename")

            if not audio_bytes:
                await self._reply_text(frame, "语音文件下载失败，请重新发送 🎤")
                return

            # 2. ASR 语音转文字
            # ⚠️ 2026-10-06：企微机器人**暂不承接**语音转写，原因见文件头注释。
            # 设计里的语音承载方是后端（FunASR/SenseVoice 双通道，已实现），
            # App 也走那条链。这里诚实回告，而不是调一个装不上的 SDK。
            from yishu.observability import log_event

            log_event(
                "wechat_voice_unsupported",
                level=logging.WARNING,
                user_id=user_id,
                audio_len=len(audio_bytes),
                hint="语音转写由 backend /api/v1/asr/transcribe 承载（B5a 设计）",
            )
            await self._reply_text(
                frame,
                "语音消息我暂时还听不懂 🎤\n"
                "可以用文字直接告诉我，或到 App 里用语音记录（App 的语音会正常转写）。",
            )
            return

        except Exception as e:
            logger.error(f"语音处理失败: {e}", exc_info=True)
            await self._reply_text(frame, "语音处理遇到了问题，请稍后再试或用文字发送 😊")

    async def _on_image(self, frame: dict[str, Any]):
        """处理图片消息"""
        user_id = self._get_user_id(frame)
        logger.info(f"收到图片消息 from {user_id}")
        await self._record_user(frame)
        await self._reply_text(
            frame,
            "收到图片啦！不过目前我主要支持文字记录，你可以用文字描述一下这张图片的内容，我来帮你保存 📸"
        )

    async def _on_enter_chat(self, frame: dict[str, Any]):
        """处理进入会话事件"""
        user_id = self._get_user_id(frame)
        logger.info(f"用户进入会话: {user_id}")
        await self._record_user(frame)
        welcome = (
            "✨ 你好！我是「忆光」，你的个人AI记忆助手。\n\n"
            "我可以帮你：\n"
            "📝 记录生活中的碎片信息（想法、经历、情绪、灵感等）\n"
            "🎤 也支持语音输入，直接发语音我来帮你转成文字保存\n"
            "🔍 搜索和回顾历史记忆\n"
            "📊 每晚十点自动推送今日回顾\n\n"
            "直接跟我分享，或者发一段语音吧 💫"
        )
        await self._reply_text(frame, welcome)

    @staticmethod
    def _get_user_id(frame: dict[str, Any]) -> str:
        """从帧中提取用户ID"""
        body = frame.get("body") or {}
        from_info = body.get("from") or {}
        return from_info.get("user_id") or "default_user"

    async def _reply_text(self, frame: dict[str, Any], text: str):
        """回复文本消息"""
        if self.client:
            await self.client.reply(
                frame,
                {"msgtype": "text", "text": {"content": text}}
            )
            logger.info(f"回复发送成功: {text[:50]}...")

    async def _process_message(self, content: str, user_id: str) -> str:
        """处理用户消息 - 调用 Agent"""
        if self.agent is None:
            self._init_agent()

        if self.agent is None:
            return "抱歉，服务暂时不可用，请稍后再试 😊"

        try:
            # 注入 user_id 到 request_context，实现数据隔离
            ctx = new_context(method="wechat_message")
            ctx.user_id = user_id
            request_context.set(ctx)

            # 调用 Agent 处理消息 (thread_id = user_id 实现对话隔离)
            result = await self.agent.ainvoke(
                {"messages": [("user", content)]},
                {"configurable": {"thread_id": user_id}}
            )

            if hasattr(result, "messages") and result.messages:
                last_message = result.messages[-1]
                if hasattr(last_message, "content"):
                    return last_message.content

            return "已收到你的消息 💫"

        except Exception as e:
            logger.error(f"Agent处理消息失败: {e}")
            return "抱歉，处理消息时遇到了一些问题，请稍后再试 😊"


async def run_wechat_service():
    """运行企业微信消息服务（单例模式）"""
    global _service_instance
    logger.info("启动企业微信消息服务...")
    _service_instance = WeChatMemoryService()
    await _service_instance.start()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_wechat_service())
