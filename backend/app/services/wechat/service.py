"""微信客服消息处理（S4-01/03/04 · F6）

- msg_id 幂等：wechat_messages.msg_id UNIQUE——重复回调只入库一次（不丢/不重 99.9% 门禁）
- 入库：text 直接入 contents（来源=wechat）；image/voice 记 wechat_messages
  （媒体文件需先下载，MVP 记 media_id，下载接 COS/队列后置）
- 敏感识别：B5-b 护栏在入库时同步执行，命中标记不展示（不进云端镜像）
- 软删本条：微信端"删掉"→ 软删除标记
"""
from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, WechatMessage
from app.services.external import moderate

logger = logging.getLogger("yishu.wechat")

VALID_SOURCES = ("active", "echo", "org")


def process_incoming(db: Session, msg: dict, user_id: str | None = None) -> dict:
    """处理企微回调消息（幂等：msg_id 已存在则跳过）

    user_id=None（未绑定 unionid）→ 只记录 wechat_messages，不建 contents；
    绑定后由 S4-01 后续任务回填归属。
    返回 {status: created|duplicate|ignored, content_id?, sensitive?}
    """
    msg_id = msg.get("msg_id")
    if not msg_id:
        raise ValueError("消息缺少 msg_id")
    existed = db.execute(
        select(WechatMessage).where(WechatMessage.msg_id == msg_id)
    ).scalar_one_or_none()
    if existed is not None:
        return {"status": "duplicate", "msg_id": msg_id}

    if msg.get("msg_type") not in ("text", "image", "voice"):
        return {"status": "ignored", "msg_id": msg_id}

    record = WechatMessage(
        msg_id=msg_id,
        user_id=user_id,
        msg_type=msg["msg_type"],
        content=msg.get("content"),
        media_id=msg.get("media_id"),
        status="processed",
    )
    db.add(record)

    result: dict = {"status": "created", "msg_id": msg_id}
    if user_id and msg["msg_type"] == "text" and msg.get("content"):
        text = msg["content"]
        # 敏感识别（B5-b 护栏；mock 模式放行，真实模式 fail-safe）
        guard = moderate(text)
        result["sensitive"] = not guard["pass"]
        content = Content(
            id=str(uuid.uuid4()),
            user_id=user_id,
            content_type="text",
            text=text,
            source="wechat",
            sensitive_status="敏感" if not guard["pass"] else "正常",
            status="done",
        )
        db.add(content)
        result["content_id"] = content.id
    db.commit()
    return result


def soft_delete_by_msg(db: Session, msg_id: str, user_id: str | None = None) -> bool:
    """微信端软删本条（F6：只删本条；status → deleted）

    user_id 传入时校验归属（审查 CRITICAL 修复）：他人消息视为不存在。
    """
    record = db.execute(
        select(WechatMessage).where(WechatMessage.msg_id == msg_id)
    ).scalar_one_or_none()
    if record is None:
        return False
    if user_id is not None and str(record.user_id) != str(user_id):
        return False
    record.status = "deleted"
    db.commit()
    return True


def find_memories(db: Session, user_id: str, query: str, limit: int = 3) -> dict:
    """微信"找"（S4-02）：消息解析 → F5 RAG 搜索 → 回复文本

    沙箱可测：不依赖真实企微回调，直接调用本函数验证全链路与 10s/3s 门禁；
    真实回调接入后由回调处理器调用并组装被动回复 XML。
    """
    import time

    from app.schemas.search import SearchQuery
    from app.services.rag import search as rag_search

    if not query or not query.strip():
        return {"query": query, "reply": "想问什么？发一句描述试试～", "hits": 0, "latency_ms": 0, "degraded": False}

    t0 = time.perf_counter()
    result = rag_search(SearchQuery(q=query.strip(), limit=limit), db=db, user_id=user_id)
    latency_ms = int((time.perf_counter() - t0) * 1000)
    lines = []
    for i, h in enumerate(result.hits[:limit], 1):
        text = (h.text or "")[:80].replace("\n", " ")
        lines.append(f"{i}. {text}")
    reply = "没有找到相关记忆～换个说法试试？" if not lines else "找到啦：\n" + "\n".join(lines)
    return {
        "query": query,
        "reply": reply,
        "hits": len(result.hits),
        "latency_ms": latency_ms,
        "degraded": result.degraded,
    }
