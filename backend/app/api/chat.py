"""AI 对话域路由（AG5）：把客户端契约接到 Agent 服务。

**契约原文**（`client/components/TabAi/TabAi.uvue:6-8`，09-01 峰宝拍板，contract-first）：
    POST /api/v1/chat/messages   { conversation_id?, content, attachments?[] } → { reply }
    GET  /api/v1/chat/replies/:id（流式分片轮询/SSE，形态由 reply.kind 决定：
         bubble | plain+chips | cards | confirm | typing）
    【本实现】响应统一包在既有 `ApiResponse` 外壳里（`{code,message,data:{conversation_id,reply,…}}`）——
    与本仓其余端点一致；客户端按 `data.reply` 取值（AG6 接线时同步契约注释）。

接线要点：
  1. **JWT → 可信 user_id**：user_id 只源于 `get_current_user`。agent 服务自身不鉴权用户身份
     （见 `agent/UPSTREAM.md` 五-1），故它不得暴露公网，本后端是唯一入口。
  2. **会话隔离**：`conversation_id` 缺省新建；**访问他人/不存在的会话一律 404**
     （语义同 CONTENT_010 —— 不区分"不存在"与"无权"，避免枚举他人会话）。
  3. **失败不伪造回复**：agent 不可用 → 502/504（CHAT_001/002）。用户消息**仍落库**——
     便于复盘"用户问了但没答上"，也避免用户重问时上下文断裂。
  4. **流式（SSE）本批未实现**：POST 内联返回完整回复；`GET /replies/{id}` 返回该条已落库回复，
     客户端可先用非流式路径。真流式需 agent 侧 SSE 输出，另批（已登记排期 AG6 余项）。
"""
from __future__ import annotations

import logging
import time
import uuid

from fastapi import Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_CHAT_001, ERR_CHAT_002, ERR_CHAT_003, ERR_CHAT_004, ApiError
from app.db.models import ChatMessage, User
from app.db.session import get_db
from app.schemas.chat import (
    ChatHistoryItemOut,
    ChatHistoryOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatReplyOut,
)
from app.schemas.common import ApiResponse
from app.services.external.agent import AgentServiceError, call_agent_chat
from app.services.llm_ops.output_guard import screen_generated

logger = logging.getLogger("yishu.api.chat")

router = make_router(prefix="/api/v1/chat", tags=["chat"])


def _to_reply(row: ChatMessage) -> ChatReplyOut:
    """ORM → 契约回复对象（chips/cards 暂空：agent 目前只回文本，结构化形态另批）。"""
    return ChatReplyOut(
        id=str(row.id),
        kind=row.kind,  # type: ignore[arg-type]
        text=row.content,
        chips=list((row.payload or {}).get("chips") or []),
        cards=list((row.payload or {}).get("cards") or []),
        created_at=row.created_at.isoformat(),
    )


def _kind_for(text: str) -> str:
    """回复形态判定：多行 = plain（直铺长答，V3 SOP 的确认块即多行），单行 = bubble。

    `cards`/`confirm`/`typing` 需要 agent 侧结构化输出（当前只回纯文本），故本批不会产生；
    客户端按契约对未知 kind 走默认渲染即可（AG6 会同步契约注释）。
    """
    return "plain" if "\n" in text.strip() else "bubble"


def _resolve_conversation_id(db: Session, user_id: str, requested: str | None) -> str:
    """缺省新建会话；显式传入时必须是**本人已有**的会话，否则 404（IDOR 防护）。"""
    if not requested:
        return str(uuid.uuid4())
    try:
        normalized = str(uuid.UUID(requested))
    except (ValueError, AttributeError, TypeError) as exc:
        # 与 deps.uuid4_str 同语义：畸形 ID 视同不存在（不暴露内部错误路径）
        raise ApiError(ERR_CHAT_003, "会话不存在", http=404) from exc
    exists = db.execute(
        select(ChatMessage.id).where(
            ChatMessage.user_id == user_id,
            ChatMessage.conversation_id == normalized,
        ).limit(1)
    ).scalar_one_or_none()
    if exists is None:
        raise ApiError(ERR_CHAT_003, "会话不存在", http=404)
    return normalized


@router.post("/messages", response_model=ApiResponse[ChatMessageOut])
def post_message(
    req: ChatMessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发一条消息，返回智能体回复（非流式；失败不伪造）。"""
    started = time.monotonic()
    conversation_id = _resolve_conversation_id(db, str(user.id), req.conversation_id)

    # 1) 先落用户消息（agent 失败也要留痕："问了但没答上"可复盘）
    user_row = ChatMessage(
        user_id=str(user.id),
        conversation_id=conversation_id,
        role="user",
        content=req.content,
        kind="user",
        payload={"attachments": [a.model_dump() for a in req.attachments]} if req.attachments else None,
    )
    db.add(user_row)
    db.commit()

    # 2) 调 agent（**单次尝试**：见 services/external/agent.py 的幂等性说明，不套重试）
    try:
        result = call_agent_chat(user_id=str(user.id), message=req.content, thread_id=conversation_id)
    except AgentServiceError as exc:
        code = ERR_CHAT_002 if exc.http_status == 504 else ERR_CHAT_001
        logger.warning("agent 调用失败 user=%s conv=%s kind=%s", user.id, conversation_id, exc.kind)
        raise ApiError(code, str(exc), http=exc.http_status) from exc

    # 2.5) 生成态输出护栏（D10-8 · 2026-09-25 用户拍板「按建议来」）：
    # Agent 回复**直接展示在对话页**且量小 ⇒ 走**完整链路**（规则层 + LLM 级）。
    # 命中 ⇒ **不落库、不返回该文本**（复用 §2.3 既定口径：agent 不可用时不伪造回复，
    # 这里同样绝不把未过审文本推给用户）；用户消息已落库，可复盘"问了但被拦"。
    verdict = screen_generated(result.text, deep=True)
    if not verdict["pass"]:
        logger.warning(
            "agent 回复未通过生成态护栏 user=%s conv=%s detector=%s",
            user.id,
            conversation_id,
            verdict["detector"],
        )
        raise ApiError(ERR_CHAT_004, "AI 回复未通过安全审核，已拦截", http=422)

    # 3) 落回复
    reply_row = ChatMessage(
        user_id=str(user.id),
        conversation_id=conversation_id,
        role="assistant",
        content=result.text,
        kind=_kind_for(result.text),
        agent_latency_ms=result.latency_ms,
    )
    db.add(reply_row)
    db.commit()
    db.refresh(reply_row)

    return ApiResponse(
        data=ChatMessageOut(
            conversation_id=conversation_id,
            reply=_to_reply(reply_row),
            latency_ms=int((time.monotonic() - started) * 1000),
        )
    )


@router.get("/replies/{reply_id}", response_model=ApiResponse[ChatReplyOut])
def get_reply(
    reply_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """按 ID 取一条**本人**的智能体回复（客户端轮询/SSE 取回复的落点）。"""
    try:
        normalized = str(uuid.UUID(reply_id))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ApiError(ERR_CHAT_003, "回复不存在", http=404) from exc
    row = db.execute(
        select(ChatMessage).where(
            ChatMessage.id == normalized,
            ChatMessage.user_id == str(user.id),
            ChatMessage.role == "assistant",
        )
    ).scalar_one_or_none()
    if row is None:
        raise ApiError(ERR_CHAT_003, "回复不存在", http=404)
    return ApiResponse(data=_to_reply(row))


@router.get("/conversations/{conversation_id}/messages", response_model=ApiResponse[ChatHistoryOut])
def get_history(
    conversation_id: str,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """取本会话历史（时间正序）——客户端进入会话时回填，供"问 AI 带上下文"。"""
    normalized = _resolve_conversation_id(db, str(user.id), conversation_id)
    rows = db.execute(
        select(ChatMessage)
        .where(ChatMessage.user_id == str(user.id), ChatMessage.conversation_id == normalized)
        .order_by(ChatMessage.created_at.asc())
    ).scalars().all()
    return ApiResponse(
        data=ChatHistoryOut(
            conversation_id=normalized,
            messages=[
                ChatHistoryItemOut(
                    id=str(r.id), role=r.role, kind=r.kind, text=r.content, created_at=r.created_at.isoformat()
                )
                for r in rows
            ],
        )
    )
