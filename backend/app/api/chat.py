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
  4. **流式（SSE）**：`POST /messages/stream`（AG6 余项 · 2026-10-09）。非流式 `POST /messages`
     保留（脚本/降级路径/既有测试），两者**共用同一套鉴权、会话隔离、护栏与落库口径**。
     `GET /replies/{id}` 仍返回该条已落库回复（断线重取用）。
"""
from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from typing import Any

from fastapi import Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_CHAT_001, ERR_CHAT_002, ERR_CHAT_003, ERR_CHAT_004, ApiError
from app.db.models import ChatMessage, User
from app.db.session import SessionLocal, get_db
from app.schemas.chat import (
    ChatHistoryItemOut,
    ChatHistoryOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatReplyOut,
)
from app.schemas.common import ApiResponse
from app.services.external.agent import AgentServiceError, call_agent_chat, stream_agent_chat
from app.services.llm_ops.output_guard import rule_screen, screen_generated

logger = logging.getLogger("yishu.api.chat")

router = make_router(prefix="/api/v1/chat", tags=["chat"])


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
        result = call_agent_chat(user_id=str(user.id), message=req.content, conversation_id=conversation_id)
    except AgentServiceError as exc:
        code = ERR_CHAT_002 if exc.http_status == 504 else ERR_CHAT_001
        logger.warning("agent 调用失败 user=%s conv=%s kind=%s", user.id, conversation_id, exc.kind)
        raise ApiError(code, str(exc), http=exc.http_status) from exc

    # 2.4) 空终稿守卫（2026-10-11 B-1 配套）：agent 现已保证"无正文返回 502"，此处再兜一层
    # （与流式端点"两处都守"同口径——端点不依赖适配器/上游实现细节）：
    # 空文本若继续走护栏必 pass ⇒ 落一条 content='' 的 assistant 行（空气泡+脏历史）。
    if not result.text.strip():
        logger.warning("agent 返回空回复 user=%s conv=%s", user.id, conversation_id)
        raise ApiError(ERR_CHAT_001, "AI 回复未完成，请稍后重试", http=502)

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

    # 3) 落回复（含结构化附加：cards/chips —— 形态由 `_kind_for` 统一判定）
    reply_payload = _reply_payload(result.meta)
    cards = (reply_payload or {}).get("cards") or []
    chips = (reply_payload or {}).get("chips") or []
    reply_row = ChatMessage(
        user_id=str(user.id),
        conversation_id=conversation_id,
        role="assistant",
        content=result.text,
        kind=_kind_for(result.text, cards, chips),
        payload=reply_payload,
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


@router.post("/messages/stream")
def post_message_stream(
    req: ChatMessageIn,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """发一条消息并**流式**取回复（SSE · AG6 余项 2026-10-09）。

    与 `POST /messages` 的关系：**同一套鉴权 / 会话隔离 / 护栏 / 落库口径**，
    差别只在传输形态。非流式端点保留（脚本、降级路径与既有测试都在用）。

    帧格式（`event:` + 单行 JSON `data:`）：
      · `status`  {"stage":"accepted"}                       —— 链路已接（首帧，立即）
      · `preview` {"text":"<累计全文>"}                       —— **累计**而非增量
      · `tool`    {"phase":"started"|"done","name":…}         —— 工具状态（UI"在翻记忆"）
      · `replace` {"text":"<整段>"}                           —— 校验重试 ⇒ 预览作废覆盖
      · `done`    {"conversation_id","reply":{…},"latency_ms"} —— **终稿**（客户端以此收尾）
      · `error`   {"code","message"}                          —— CHAT_001/002/004/INTERNAL_ERROR

    为什么 `preview` 推**累计全文**而不是增量分片：客户端侧桥接（蒸汽模式）可能丢帧，
    增量一旦丢一帧，拼出来的回复就是**错字**（比慢更糟）；累计帧每帧自洽，
    丢帧只表现为"晚一拍"，下一帧就追平。代价是每秒多几百字节，值得。

    ⚠️ 三条不可退让的事在流式形态下仍然成立（本端点全部复杂度都在这上面）：
      1. **用户消息先落库**——agent 失败也留痕（同非流式口径）；
      2. **预览必须过规则层**：每累积一段就跑 `rule_screen`，命中即中断推流并结束。
         已推出去的前缀必然是过了规则层的 ⇒「护栏拦截不得泄漏预览」成立；
      3. **终稿仍走完整链路**（规则层 + LLM 级）：不过审 ⇒ **不落库、不返回该文本**（CHAT_004）。
    """
    conversation_id = _resolve_conversation_id(db, str(user.id), req.conversation_id)
    user_id = str(user.id)

    # 1) 先落用户消息（agent 失败也要留痕——同非流式的第 1 步）
    user_row = ChatMessage(
        user_id=user_id,
        conversation_id=conversation_id,
        role="user",
        content=req.content,
        kind="user",
        payload={"attachments": [a.model_dump() for a in req.attachments]} if req.attachments else None,
    )
    db.add(user_row)
    db.commit()

    def _frames():
        # ═══ A-1（2026-10-11 静态审查实锤）：消费+落库与**客户端连接解耦** ═══
        # 原实现把"消费 agent 流 + 护栏 + 落库"直接写在响应生成器里 ⇒ 客户端断连
        # （90s 看门狗 / 网络闪断 / 切后台被杀）时生成器被 close，agent 流随之中断，
        # **整轮 agent 运行被中途放弃、回复永不落库**——而 agent 单轮实测 56~76s，
        # 校验重试轮可破 90s，正常长回复就会触发看门狗 ⇒ 真机必踩。
        # 现改为 lessons 同款「工作线程 + 队列」：后台线程**无论如何**把 agent 流
        # 消费到 done 并落库（线程自带 SessionLocal）；生成器只负责把队列里的帧推给
        # 客户端，断连后停止投递但**不打断**后台线程。断线重取走既有
        # `GET /replies/{id}` 与历史回填（回复已落库，等一拍就能拿到）。
        events: queue.Queue[tuple[str, Any] | None] = queue.Queue(maxsize=256)
        client_gone = threading.Event()

        def _offer(item: tuple[str, Any] | None) -> None:
            """投递帧；客户端已断连则丢弃（落库流程不受影响）。"""
            while True:
                try:
                    events.put(item, timeout=0.5)
                    return
                except queue.Full:
                    if client_gone.is_set():
                        return

        def _consume_and_persist() -> None:
            started = time.monotonic()
            preview = ""
            # 2026-10-15（真机反馈「AI 对话冒出 JSON 中间消息」· 台账 §7.31）：
            # **原始累积与可见文本分离**。DeepSeek 会往 `delta.content` 写原生 DSML 工具
            # 调用标记、且标记**可能跨帧**（半截在本帧、后半截在下帧）⇒ 若直接对累计串做
            # 剥离并覆盖，已剥掉的半截标记就再也认不出来，下一帧补全后会漏给用户 ✗。
            # 故：`raw_preview` 存原文（用于识别跨帧标记），每次**全量**剥成 `preview` 再推送
            #（`strip_model_markup` 幂等 ⇒ 每帧重剥安全）。
            from app.services.model_text import strip_model_markup

            raw_preview = ""
            final_text = ""
            agent_latency_ms: int | None = None
            final_meta: dict | None = None
            # 显式开自己的会话：后台线程与请求作用域完全无关（线程隔离，不赌任何框架时序）
            session = SessionLocal()
            try:
                _offer(("status", {"stage": "accepted"}))
                for kind, payload in stream_agent_chat(
                    user_id=user_id, message=req.content, conversation_id=conversation_id
                ):
                    if kind == "delta":
                        raw_preview += str(payload)
                        # 每次对**累计原文**全量剥离 ⇒ 跨帧标记也能正确消掉（幂等）
                        preview = strip_model_markup(raw_preview)
                        verdict = rule_screen(preview)
                        if not verdict["pass"]:
                            logger.warning(
                                "流式预览命中规则层护栏 user=%s conv=%s detector=%s",
                                user_id,
                                conversation_id,
                                verdict["detector"],
                            )
                            _offer(
                                (
                                    "error",
                                    {"code": ERR_CHAT_004, "message": "AI 回复未通过安全审核，已拦截"},
                                )
                            )
                            return
                        _offer(("preview", {"text": preview}))
                    elif kind == "tool":
                        _offer(("tool", payload if isinstance(payload, dict) else {}))
                    elif kind == "replace":
                        # 校验重试：预览作废，客户端按"整体覆盖"处理（不是追加）。
                        # ⚠️ 2026-10-11 A-2（静态审查实锤）：replace 文本同样出自 LLM，**必须过
                        # 规则层**——原实现直接转发，「护栏拦截不得泄漏预览」在 replace 路径上
                        # 被整体绕过（重试轮命中敏感词照样推到客户端屏幕）。命中即中断，同 delta。
                        # 另外 preview 基准置为 replace 全文（而非清空）：若 replace 之后再来
                        # delta（当前 agent 实现不会，防御性），累计文本不丢 replace 前缀。
                        raw_preview = str(payload)
                        preview = strip_model_markup(raw_preview)
                        verdict = rule_screen(preview)
                        if not verdict["pass"]:
                            logger.warning(
                                "流式 replace 帧命中规则层护栏 user=%s conv=%s detector=%s",
                                user_id,
                                conversation_id,
                                verdict["detector"],
                            )
                            _offer(
                                (
                                    "error",
                                    {"code": ERR_CHAT_004, "message": "AI 回复未通过安全审核，已拦截"},
                                )
                            )
                            return
                        _offer(("replace", {"text": preview}))
                    elif kind == "done":
                        data = payload if isinstance(payload, dict) else {}
                        # 终稿同样剥（它既进 `done` 帧、**也会落库** ⇒ 库里不能留标记）
                        final_text = strip_model_markup(str(data.get("reply") or ""))
                        raw_latency = data.get("latency_ms")
                        agent_latency_ms = raw_latency if isinstance(raw_latency, int) else None
                        raw_meta = data.get("meta")
                        final_meta = raw_meta if isinstance(raw_meta, dict) else None

                # 1.5) 终稿**必须存在**：流式特有的新失败面——监听到了 preview 却没有终稿
                # （agent 进程被杀 / 中间层截断）极易被当成"正常收尾" ⇒ 落一条 content='' 的
                # assistant 行：用户看到空气泡、历史里永久留脏数据。
                # 适配器层已把"未见 done"当 unavailable 抛出（stream_agent_chat 的 saw_done 守卫），
                # 这里再兜一层（两处都守的理由：端点不该依赖适配器的实现细节才能保证不落脏数据）。
                if not final_text.strip():
                    logger.warning("agent 流未返回终稿 user=%s conv=%s", user_id, conversation_id)
                    _offer(("error", {"code": ERR_CHAT_001, "message": "AI 回复未完成，请稍后重试"}))
                    return

                # 2) 终稿护栏：与非流式**同一口径**（规则层 + LLM 级；命中即不落库、不返回）
                verdict = screen_generated(final_text, deep=True)
                if not verdict["pass"]:
                    logger.warning(
                        "agent 流式回复未通过生成态护栏 user=%s conv=%s detector=%s",
                        user_id,
                        conversation_id,
                        verdict["detector"],
                    )
                    _offer(
                        (
                            "error",
                            {"code": ERR_CHAT_004, "message": "AI 回复未通过安全审核，已拦截"},
                        )
                    )
                    return

                # 3) 落回复 + 终稿帧（形态与结构化附加的口径同非流式端点）。
                # 落库**必然先于** done 帧投递（同序保证：客户端收到 done 时数据已持久化）
                reply_payload = _reply_payload(final_meta)
                cards = (reply_payload or {}).get("cards") or []
                chips = (reply_payload or {}).get("chips") or []
                reply_row = ChatMessage(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    role="assistant",
                    content=final_text,
                    kind=_kind_for(final_text, cards, chips),
                    payload=reply_payload,
                    agent_latency_ms=agent_latency_ms,
                )
                session.add(reply_row)
                session.commit()
                session.refresh(reply_row)
                _offer(
                    (
                        "done",
                        {
                            "conversation_id": conversation_id,
                            "reply": _to_reply(reply_row).model_dump(),
                            "latency_ms": int((time.monotonic() - started) * 1000),
                        },
                    )
                )
            except AgentServiceError as exc:
                code = ERR_CHAT_002 if exc.http_status == 504 else ERR_CHAT_001
                logger.warning(
                    "agent 流式调用失败 user=%s conv=%s kind=%s", user_id, conversation_id, exc.kind
                )
                _offer(("error", {"code": code, "message": str(exc)}))
            except Exception:  # noqa: BLE001 —— 落库/序列化等未预期失败：如实报错，不吞
                logger.exception("流式回复收尾失败 user=%s conv=%s", user_id, conversation_id)
                session.rollback()
                _offer(("error", {"code": _SSE_INTERNAL_CODE, "message": _SSE_INTERNAL_MESSAGE}))
            finally:
                session.close()
                _offer(None)  # 结束哨兵（断连时被 _offer 丢弃——生成器那边已无人等待）

        threading.Thread(
            target=_consume_and_persist, name="chat-stream-consumer", daemon=True
        ).start()
        try:
            while True:
                try:
                    item = events.get(timeout=_KEEPALIVE_INTERVAL_S)
                except queue.Empty:
                    # ═══ B3（2026-10-11 静态审查实锤）：保活心跳 ═══
                    # 客户端 idle 看门狗（90s 无任何帧即判超时）**必须**有喂帧来源。
                    # 原实现两侧都以为"对方在发 ping"：agent 的 `: ping` 注释帧在适配器
                    # `_iter_sse` 里就被丢弃（agent.py 的 `line.startswith(":")` 分支），
                    # backend 自身也不发 —— 于是长思考/工具静默轮超过 90s 会被客户端
                    # 误杀（并 abort ⇒ 用户看到失败，而后端其实还在正常跑）。
                    # 注释帧格式 `: keepalive\n\n`：客户端按 `\n\n` 切帧后解析得空
                    # event/data ⇒ 无业务分支命中，但**已触发其看门狗续命**（handleFrame
                    # 入口即 resetWatchdog）—— 这正是我们要的唯一效果。
                    yield ": keepalive\n\n"
                    continue
                if item is None:
                    return
                yield _sse(item[0], item[1])
        finally:
            # 正常结束/客户端断连（GeneratorExit）都置位：后台线程据此停止投递帧，
            # 但**继续消费 agent 流到 done 并落库**——回复不丢，断线重取有据可依。
            client_gone.set()

    return StreamingResponse(
        _frames(),
        media_type="text/event-stream",
        headers={
            # 中间层（nginx/隧道）默认会缓冲整段响应，那等于把流式退化成非流式
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
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
                    id=str(r.id),
                    role=r.role,
                    kind=r.kind,
                    text=r.content,
                    created_at=r.created_at.isoformat(),
                    chips=list((r.payload or {}).get("chips") or []),
                    cards=list((r.payload or {}).get("cards") or []),
                )
                for r in rows
            ],
        )
    )
