"""微信客服消息处理（S4-01/03/04 · F6 · Wave3 AgentG 扩展）

- msg_id 幂等：wechat_messages.msg_id UNIQUE——重复回调只入库一次（不丢/不重 99.9% 门禁）
- 入库：text 直接入 contents（来源=wechat）；image/voice 记 wechat_messages + 媒体上云
- 媒体云端原件（Wave3 AgentG · audit #8 缺口修复）：
  image/voice → 下载企微媒体到对象存储（cos_key 落 Content）→ 图片 CI 审核（敏感排除）
  → 与 photo 同链路入管线（process_content）+ 缩略图预生成（image）
  凭证未配置（WECHAT_* / TENCENT_* 缺失）→ mock 媒体字节 + CI 默认放行（代码先行，
  拿 key 后零切换；真实回调与真实审核在配置到位后自动启用）
- 敏感识别（B5-b 护栏 · Wave4-L）：入库时经 content_safety 适配器（provider 可切换，
  见 external/content_safety.py）同步执行——文本=规则+护栏/阿里云，图片=CI/阿里云；
  命中 → sensitive_status 标记 + 不进云端镜像（与现有敏感排除合并）
- 软删本条：微信端"删掉"→ 软删除标记
- 内容安全 fail-safe：审核服务故障只告警 + 默认放行，不因审核不可用丢消息
  （微信收消息可靠性优先，M3 门禁 99.9%）

表需求（登记给集成 Agent）：wechat_messages 建议补 content_id/cos_key 列实现直接关联
（当前用 Content.extra.wechat_msg_id 反向关联，无需迁移即可工作）。
"""
from __future__ import annotations

import io
import logging
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Content, WechatMessage
from app.services import thumbnails
from app.services.external.content_safety import get_content_safety
from app.services.wechat import ports

logger = logging.getLogger("yishu.wechat")

# ---- 媒体网关端口（R1#9 依赖反转）----
# 业务只依赖 ports.MediaGatewayPort 契约；默认驱动 = 本模块 download_media
# （企微 HTTP 实现），可经 set_media_gateway 注入替身。默认网关方法内按模块全局
# 解析 download_media（调用时查找）→ 测试 monkeypatch wx.download_media 仍生效。
# 用 dict 容器持有（避免 global 语句，ruff PLW0603）。
_media_gateway_ref: dict[str, ports.MediaGatewayPort | None] = {"gateway": None}


class _DefaultMediaGateway:
    """默认媒体网关驱动：委托模块级 download_media/_corp_access_token（企微 HTTP）"""

    def get_access_token(self) -> str | None:
        return _corp_access_token()

    def download_media(self, media_id: str, msg_type: str) -> bytes:
        return download_media(media_id, msg_type)


def get_media_gateway() -> ports.MediaGatewayPort:
    """当前媒体网关端口实现（默认 = 企微 HTTP 驱动；可 set_media_gateway 替换）"""
    gw = _media_gateway_ref["gateway"]
    if gw is None:
        gw = _DefaultMediaGateway()
        _media_gateway_ref["gateway"] = gw
    return gw


def set_media_gateway(gateway: ports.MediaGatewayPort | None) -> None:
    """注入/重置媒体网关（None 恢复默认驱动；测试替身注入点）"""
    _media_gateway_ref["gateway"] = gateway

VALID_SOURCES = ("active", "echo", "org")

# 企微媒体下载（真实模式；未配置走 mock）
WECOM_API_BASE = "https://qyapi.weixin.qq.com/cgi-bin"

# 企微 access_token 有效期 7200s（S6-9）：提前 200s 刷新余量，避免边界失效
_ACCESS_TOKEN_TTL_SEC = 7200
_ACCESS_TOKEN_REFRESH_MARGIN = 200

# 进程级 token 缓存（S6-9）：{token, expires_at(monotonic)}；并发进程各自缓存（token 可并发有效）
_token_cache: dict = {}


def _corp_access_token() -> str | None:
    """企业 access_token（media/get 必需）——进程内缓存 + 过期失效重取（S6-9）

    需企微应用凭证：WECHAT_CORP_ID + 应用 Secret。
    凭证未配置或 MOCK_EXTERNAL_AI=true → 返回 None（调用方走 mock）。
    """
    from app.core.config import settings

    if settings.mock_external_ai or not (settings.wechat_corp_id and settings.wechat_token):
        return None
    import time

    now = time.monotonic()
    cached = _token_cache.get("token")
    if cached is not None and _token_cache.get("expires_at", 0) > now:
        return cached
    import httpx

    resp = httpx.get(
        f"{WECOM_API_BASE}/gettoken",
        params={"corpid": settings.wechat_corp_id, "corpsecret": settings.wechat_token},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode") not in (0, None):
        raise RuntimeError(f"企微 gettoken 失败: {data.get('errmsg')}")
    token = data.get("access_token")
    if token:
        expires_in = int(data.get("expires_in") or _ACCESS_TOKEN_TTL_SEC)
        _token_cache["token"] = token
        _token_cache["expires_at"] = now + max(
            60, expires_in - _ACCESS_TOKEN_REFRESH_MARGIN
        )
    return token


def _invalidate_access_token() -> None:
    """token 失效显式清缓存（S6-9：40014/42001 等失效错误后强制重取）"""
    _token_cache.pop("token", None)
    _token_cache.pop("expires_at", None)


def _media_extension(msg_type: str) -> str:
    # 企微语音固定 amr；图片统一 jpg（COS 对象不强制真实容器格式）
    return ".amr" if msg_type == "voice" else ".jpg"


def _mock_image_bytes() -> bytes:
    """64×64 纯色 JPEG（PIL 生成，无需网络/凭证，链路可测）"""
    from PIL import Image

    img = Image.new("RGB", (64, 64), (120, 80, 200))
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


def _mock_voice_bytes() -> bytes:
    """0.1s 静音 WAV（合法 RIFF 头，管线 ASR mock 可吃）"""
    import wave

    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)
    return buf.getvalue()


def download_media(media_id: str, msg_type: str) -> bytes:
    """下载企微媒体（image/voice）→ 字节

    真实模式：GET /media/get?access_token=..&media_id=..；失败抛 RuntimeError。
    凭证缺失 / mock 模式：返回可测试的 mock 字节（真实模式绝不混入 mock——
    mock 只出现在未配置或 MOCK_EXTERNAL_AI=true 的沙箱/联调环境）。
    S6-9：token 失效错误（40014/42001）→ 清缓存重取一次（失效重取）。
    """
    token = _corp_access_token()
    if token is None:
        return _mock_image_bytes() if msg_type == "image" else _mock_voice_bytes()

    import httpx

    def _fetch(_token: str) -> httpx.Response:
        return httpx.get(
            f"{WECOM_API_BASE}/media/get",
            params={"access_token": _token, "media_id": media_id},
            timeout=30,
        )

    resp = _fetch(token)
    # 企微媒体下载失败时返回 JSON {errcode, errmsg}（成功时为二进制流）
    content_type = resp.headers.get("content-type", "")
    if "json" in content_type:
        data = resp.json()
        # 失效重取：access_token 过期/无效 → 清缓存重取一次
        if data.get("errcode") in (40014, 42001):
            _invalidate_access_token()
            new_token = _corp_access_token()
            if new_token:
                resp = _fetch(new_token)
                content_type = resp.headers.get("content-type", "")
                if "json" not in content_type:
                    if resp.content:
                        return resp.content
        raise RuntimeError(f"企微媒体下载失败: {data.get('errmsg', resp.status_code)}")
    resp.raise_for_status()
    if not resp.content:
        raise RuntimeError("企微媒体返回空内容")
    return resp.content


def _audit_image(cos_key: str) -> dict:
    """图片内容审核（S4-03 敏感排除）：经 content_safety 适配器（provider 可切换）

    命中任意敏感标签 → pass=False。适配器按 provider 处理降级（当前默认 tencent_ci：
    CI 不可用 → 默认放行并告警，与 pipeline 对 CI 失败静默降级一致；微信收消息链路
    不因审核故障丢消息）。
    """
    try:
        return get_content_safety().check_image(cos_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("图片内容安全不可用，默认放行 cos_key=%s: %s", cos_key, exc)
        return {"pass": True, "labels": []}


def _build_content_extra(msg: dict, media_id: str) -> dict:
    """微信来源追溯（无新列也可反向关联）：extra.wechat_msg_id/wechat_media_id"""
    return {
        "wechat_msg_id": msg.get("msg_id"),
        "wechat_media_id": media_id,
    }


def _check_text_safe(text: str) -> dict:
    """文本内容审核（B5-b 护栏 · Wave4-L）：content_safety 适配器，fail-safe 放行

    审核服务异常（如显式配置 aliyun 但缺 key）→ 告警 + 默认放行，不丢消息。
    """
    try:
        return get_content_safety().check_text(text)
    except Exception as exc:  # noqa: BLE001
        logger.warning("文本内容安全不可用，默认放行: %s", exc)
        return {"pass": True, "labels": []}


def _process_media(db: Session, record: WechatMessage, msg: dict, user_id: str) -> dict:
    """媒体上云：下载 → COS（cos_key 落 Content）→ 图片 CI 审核 → 入管线

    返回结果片段并入 process_incoming 外层结果：
      {"media": "ok", "cos_key": ..., "content_id": ...} / {"media": "failed", ...}
      / {"media": "blocked", "sensitive": True}
    """
    from app.services.external.storage import get_storage_backend
    from app.services.photo_content import safe_enqueue_unique
    from app.services.pipeline import process_content

    media_id = msg.get("media_id")
    if not media_id:
        return {"media": "skipped", "reason": "no media_id"}

    try:
        data = get_media_gateway().download_media(media_id, msg["msg_type"])
    except Exception as exc:  # noqa: BLE001 —— 下载失败不影响消息入库（只标记）
        logger.warning("企微媒体下载失败 msg=%s: %s", msg.get("msg_id"), exc)
        record.status = "media_failed"
        db.commit()
        return {"media": "failed", "error": type(exc).__name__}

    cos_key = f"wechat/{user_id}/{msg.get('msg_id')}{_media_extension(msg['msg_type'])}"
    get_storage_backend().put_object(cos_key, data)

    # 图片敏感排除（S4-03：命中不进云端镜像）
    if msg["msg_type"] == "image":
        audit = _audit_image(cos_key)
        if not audit["pass"]:
            record.status = "sensitive"
            db.commit()
            return {"media": "blocked", "sensitive": True, "labels": audit["labels"]}

    extra = _build_content_extra(msg, media_id)
    if msg["msg_type"] == "voice":
        extra["file_name"] = f"{media_id}{_media_extension('voice')}"
    content = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="photo" if msg["msg_type"] == "image" else "voice",
        cos_key=cos_key,
        extra=extra,
        source="wechat",
        status="processing",
    )
    db.add(content)
    record.status = "processed"
    try:
        db.commit()
    except Exception:  # noqa: BLE001 —— P0-6（审查 H-3）：对象已落但 DB 无记录
        # → 尽力删除防孤儿对象（企微会重推回调，msg_id 幂等兜底）
        from app.services.external.storage import best_effort_delete

        db.rollback()
        best_effort_delete(cos_key)
        raise
    db.refresh(content)

    # F4：enqueue_unique 同 content 键不重复入队（safe：失败仅记日志，P0-5）
    safe_enqueue_unique(process_content, str(content.id))
    if content.content_type == "photo":
        safe_enqueue_unique(thumbnails.generate_thumbnail_job, str(content.id))
    return {"media": "ok", "cos_key": cos_key, "content_id": str(content.id)}


def process_incoming(db: Session, msg: dict, user_id: str | None = None) -> dict:
    """处理企微回调消息（幂等：msg_id 已存在则跳过）

    user_id=None（未绑定 unionid）→ 只记录 wechat_messages（媒体也不下载），
    绑定后由 S4-01 后续任务回填归属。
    返回 {status: created|duplicate|ignored, content_id?, sensitive?, media?}
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

    # R2#13 竞态修复：msg_id 幂等改 ON CONFLICT DO NOTHING 原子插入——
    # SELECT 查重是快路径；并发同回调双请求同时插同 msg_id（UNIQUE），败者不再
    # IntegrityError 500，而是 rowcount=0 → 视作 duplicate（不重复建内容/媒体）。
    from sqlalchemy.dialects.postgresql import insert as pg_insert

    inserted = db.execute(
        pg_insert(WechatMessage)
        .values(
            msg_id=msg_id,
            user_id=user_id,
            msg_type=msg["msg_type"],
            content=msg.get("content"),
            media_id=msg.get("media_id"),
            status="processed",
        )
        .on_conflict_do_nothing(index_elements=[WechatMessage.msg_id])
    )
    if inserted.rowcount == 0:
        db.rollback()
        return {"status": "duplicate", "msg_id": msg_id}
    record = db.execute(
        select(WechatMessage).where(WechatMessage.msg_id == msg_id)
    ).scalar_one()

    result: dict = {"status": "created", "msg_id": msg_id}

    if user_id and msg["msg_type"] == "text" and msg.get("content"):
        text = msg["content"]
        # 敏感识别（B5-b 护栏 · Wave4-L）：content_safety 适配器（provider 可切换；
        # 默认 tencent_ci = 规则+护栏；aliyun 上架前启用）。审核不可用 → 放行不丢消息。
        guard = _check_text_safe(text)
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
    elif user_id and msg["msg_type"] in ("image", "voice"):
        result.update(_process_media(db, record, msg, user_id))
        return result  # _process_media 已 commit

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
