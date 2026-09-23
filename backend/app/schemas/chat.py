"""AI 对话域契约（AG5）—— **逐字段对齐客户端既有 mock 契约**（不自创）。

契约原文（`client/components/TabAi/TabAi.uvue:6-8`，09-01 峰宝拍板）：
    POST /api/v1/chat/messages   { conversation_id?, content, attachments?[] } → { reply }
    GET  /api/v1/chat/replies/:id
    reply.kind ∈ bubble | plain+chips | cards | confirm | typing
客户端据此实现渲染分支；故本文件字段名与枚举**不得偏离**（偏离即客户端白屏/走错分支）。
"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

# 契约枚举（客户端 TabAi 的渲染分支依据）
ReplyKind = Literal["bubble", "plain", "cards", "confirm", "typing", "plain+chips"]


class ChatAttachmentIn(BaseModel):
    """附件（契约里的 attachments[]）。MVP 仅记录，不解析内容。"""

    kind: Literal["image", "voice", "link", "file"]
    ref: str = Field(..., max_length=512, description="内容/对象引用（如 content_id 或 URL）")


class ChatMessageIn(BaseModel):
    """用户一条消息。`conversation_id` 缺省＝新建会话。"""

    conversation_id: str | None = Field(None, max_length=64, description="会话 ID（缺省新建）")
    content: str = Field(..., min_length=1, max_length=8000, description="用户输入")
    attachments: list[ChatAttachmentIn] = Field(default_factory=list)


class ChatReplyOut(BaseModel):
    """一条回复（POST 内联返回，亦可由 GET /replies/:id 取回）。"""

    id: str
    kind: ReplyKind = "bubble"
    text: str
    chips: list[str] = Field(default_factory=list, description="plain+chips 形态的快捷问题")
    cards: list[dict[str, Any]] = Field(default_factory=list, description="cards 形态的卡组")
    created_at: str


class ChatMessageOut(BaseModel):
    """POST /chat/messages 响应。"""

    conversation_id: str
    reply: ChatReplyOut
    latency_ms: int = Field(..., description="本后端观测到的端到端耗时（含 agent 单轮）")


class ChatHistoryItemOut(BaseModel):
    """历史条目：**必须带 role** —— 历史里既有用户消息也有回复。

    为什么不复用 ChatReplyOut：后者的 `kind` 是**回复形态族**（bubble/plain/cards/…），
    而用户消息没有"回复形态"；把 `user` 塞进 ReplyKind 会让语义混淆
    （实测踩到：history 端点用 ChatReplyOut 序列化用户消息 → pydantic literal_error）。
    """

    id: str
    role: Literal["user", "assistant"]
    kind: str = Field(..., description="assistant 侧为契约形态族；user 侧为 'user'")
    text: str
    created_at: str


class ChatHistoryOut(BaseModel):
    """会话历史（供客户端进入会话时回填；按时间正序）。"""

    conversation_id: str
    messages: list[ChatHistoryItemOut]
