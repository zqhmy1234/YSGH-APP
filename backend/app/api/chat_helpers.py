"""Chat pure helpers: reply projection, SSE framing, conversation id guard.

Extracted from ``chat.py`` to keep the route module under the filesize ratchet;
these functions do not depend on the router or FastAPI request state.
"""
from __future__ import annotations

import json
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import ERR_CHAT_003, ApiError
from app.db.models import ChatMessage, UserAIPersona
from app.domain.crisis.record import SqlCrisisPort
from app.domain.crisis.scan import CrisisSignal, t2_reply, t3_reply
from app.domain.persona import relationship_note, snapshot_from_row
from app.schemas.chat import ChatReplyOut


def _persona_payload(db: Session, user_id: str) -> dict[str, object]:
    """Read one snapshot; the Agent receives prose, never the 0/1/2 positions."""
    row = db.get(UserAIPersona, user_id)
    snapshot = snapshot_from_row(user_id, row)
    return {
        "role_id": snapshot.role_id,
        "relationship_note": relationship_note(snapshot.choices),
        "version": snapshot.version,
    }


def _to_reply(row: ChatMessage) -> ChatReplyOut:
    """ORM → 契约回复对象（chips/cards 从 `payload` 取；2026-10-09 起 agent 会产结构化附加）。"""
    return ChatReplyOut(
        id=str(row.id),
        kind=row.kind,  # type: ignore[arg-type]
        text=row.content,
        chips=list((row.payload or {}).get("chips") or []),
        cards=list((row.payload or {}).get("cards") or []),
        created_at=row.created_at.isoformat(),
    )


def _kind_for(text: str, cards: list | None = None, chips: list | None = None) -> str:
    """回复形态判定（客户端 TabAi 的渲染分支依据）。

    判定顺序（**每一步都有理由，别改顺序**）：
      1. 有 cards ⇒ `cards`：记忆卡横滑组；正文仍落库（卡片是正文的结构化投影，
         正文里可能有"想起你 X 月也记过类似的事"这类卡片装不下的信息）。
      2. 有 chips ⇒ `plain`：客户端**只有 plain / cards 两个分支**渲染 chips
         （见 TabAi 模板），单行文本判成 bubble 会让 chips 直接消失。
      3. 多行 ⇒ `plain`（直铺长答，V3 SOP 的确认块即多行）；单行 ⇒ `bubble`。

    `confirm`/`typing` 仍不由本端点产生（需确认交互/流式占位，客户端各自处理）。
    """
    if cards:
        return "cards"
    if chips:
        return "plain"
    return "plain" if "\n" in text.strip() else "bubble"


def _crisis_reply(
    db: Session,
    *,
    user_id: str,
    conversation_id: str,
    signal: CrisisSignal,
) -> ChatMessage:
    """T2/T3 short circuit: fixed script, zero LLM, one audited row.

    Persistence failure is reported in ``crisis_events.record_status`` but must
    never block the safety reply or pretend the AI contacted anyone.
    """
    reply = t3_reply() if signal.level == "t3" else t2_reply()
    SqlCrisisPort(db).record(user_id, signal)
    row = ChatMessage(
        user_id=user_id,
        conversation_id=conversation_id,
        role="assistant",
        content=reply,
        kind="bubble",
        payload={"crisis": {"level": signal.level}},
        agent_latency_ms=0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _reply_payload(meta: dict | None) -> dict | None:
    """把 agent 的 `_meta`（cards/chips）规整成落库的 `chat_messages.payload`。

    只认字段名、不认结构细节——agent 侧的卡片形状由它自己的测试守（agent/tests）。
    空 ⇒ None（`payload` 为空的对象会污染既有"payload 非空即有结构化附加"的读法）。
    """
    if not isinstance(meta, dict):
        return None
    cards = meta.get("cards")
    chips = meta.get("chips")
    payload: dict = {}
    if isinstance(cards, list) and cards:
        payload["cards"] = [c for c in cards if isinstance(c, dict)]
    if isinstance(chips, list) and chips:
        payload["chips"] = [str(c) for c in chips if str(c).strip()]
    return payload or None


def _sse(event: str, data: dict) -> str:
    """SSE 帧编码（`event:` + **单行** JSON `data:`）。

    与 agent 侧同名函数同构（agent/src/main.py::_sse）：两端都靠 `json.dumps` 保证
    data 不换行——多行 data 得拆成多条 `data:` 行，手写拼接极易把换行漏出去，
    客户端按行解析就会拿到半截 JSON（"偶尔解析失败"这种最难复现的故障）。
    """
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


# SSE 错误帧里的通用文案（与 errors.py 未处理异常处理器**逐字一致**；
# 那里是 500 JSON，这里是 200 SSE 帧，客户端不该因为传输形态不同就拿到两套措辞）
_SSE_INTERNAL_CODE = "INTERNAL_ERROR"
_SSE_INTERNAL_MESSAGE = "服务器内部错误，请稍后重试"

# SSE 保活间隔（秒）：队列静默超过该时长就补一条注释帧（见 `_frames` 内注释）。
# 取值依据：客户端 idle 看门狗为 90s —— 5s 一次心跳留足 18 倍余量，
# 同时足够低频（长回复 3 分钟也只多 36 帧，每帧 15 字节）。
_KEEPALIVE_INTERVAL_S = 5.0


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
