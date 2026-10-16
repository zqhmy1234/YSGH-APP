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


class ChatAttachment(BaseModel):
    """附件（对话请求 `attachments[]` · 2026-10-16「附件契约最小可用」冻结形状）。

    三层一致：客户端 `utils/chat_api.uts::ChatAttachment` → 本模型 → agent `ChatRequest.attachments`。
    为什么不叫旧名 `ChatAttachmentIn`/不用旧字段 `ref`：旧形状（kind/ref 四枚举）**自 08-01 起
    无任何调用方构造**（客户端从不构造、agent 也无该字段，见台账 §7.14）——按本仓既有口径
    「契约里预留 ≠ 现在能用」，收窄为图片通路**真实用到**的字段形态（审计 A3/D7 的销项）。
    为什么是 `content_id`：图片先走**既有上传链**落库拿到 content_id，caption 由本后端用既有字段读
    （不新调视觉模型，见 api/chat.py `_content_caption`）。
    """

    kind: Literal["image", "file"]
    content_id: str | None = Field(None, max_length=64, description="已落库内容的 id（上传链 complete 返回）")
    name: str | None = Field(None, max_length=255, description="展示名/文件名（file 用）")
    size: int | None = Field(None, ge=0, description="字节数（展示用，不参与业务判定）")


class ChatMessageIn(BaseModel):
    """用户一条消息（后端「发送请求」；契约文档里亦称 `ChatSendRequest`，指同一模型）。

    `conversation_id` 缺省＝新建会话。
    `include_profile`（2026-10-16）：**契约能力，当前无 UI 消费者**——原来那两枚隐私开关
    （privacy_ai_chat / privacy_echo）已于 2026-10-14 因"全仓零读零写"**整行移除**（台账 §7.18），
    故客户端恒传 True；字段保留是为了"将来重开开关时不再动三层链路"。
    ⚠️ **不要为它补 UI 开关**（没有真消费者就是新的空承诺）。
    后端**必须真的按它行事**：False ⇒ 不携带画像上下文（取值点在 api/chat.py，
    组装逻辑见 services/chat_context.py `profile_context`）。默认 True = 既有行为。
    """

    conversation_id: str | None = Field(None, max_length=64, description="会话 ID（缺省新建）")
    content: str = Field(..., min_length=1, max_length=8000, description="用户输入")
    attachments: list[ChatAttachment] = Field(default_factory=list)
    include_profile: bool = Field(
        True,
        description=(
            "是否允许后端把用户画像上下文随本轮消息带给 AI（缺省 True＝既有行为）。"
            "当前无 UI 消费者（原隐私开关 2026-10-14 整行移除），保留为契约能力。"
        ),
    )


class ChatReplyOut(BaseModel):
    """一条回复（POST 内联返回，亦可由 GET /replies/:id 取回）。"""

    id: str
    kind: ReplyKind = "bubble"
    text: str
    chips: list[str] = Field(default_factory=list, description="plain+chips 形态的快捷问题")
    cards: list[dict[str, Any]] = Field(default_factory=list, description="cards 形态的卡组")
    # 引用回溯（2026-10-17）：本条回复引用到的记忆/内容来源条目（content_id 等结构化字段）。
    # 数据源 = agent 侧从工具结果（save_memory/search_memories 等）派生的 meta["sources"]，
    # 本后端**只透传、不解析文本、不猜**（与 cards 同一哲学：只认字段名、不认结构细节）。
    # 缺省空数组 ⇒ 客户端 N=0 不渲染；agent 未发该字段的旧行为不变。
    sources: list[dict[str, Any]] = Field(default_factory=list)
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
    # 结构化附加（2026-10-09）：历史回填也要带上 chips/cards —— 否则"重启 App 后卡片变纯文本"，
    # 同一条回复在不同时间点长得不一样（用户会以为数据坏了）。用户行恒为空。
    chips: list[str] = Field(default_factory=list)
    cards: list[dict[str, Any]] = Field(default_factory=list)


class ChatHistoryOut(BaseModel):
    """会话历史（供客户端进入会话时回填；按时间正序）。"""

    conversation_id: str
    messages: list[ChatHistoryItemOut]
