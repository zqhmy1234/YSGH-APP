"""企微客服回调 API（F6 微信入口 · S4-01/02）

GET  /api/v1/wechat/callback —— URL 验证（echostr 原样返回，企微确认归属）
POST /api/v1/wechat/callback —— 收消息（验签→解密→幂等入库→"success"）
POST /api/v1/wechat/find      —— 微信"找"（S4-02：消息解析→RAG 搜索→回复，沙箱可测）
POST /api/v1/wechat/delete  —— 微信端软删本条（msg_id）

未配置 WECHAT_* 时回调一律拒绝（安全：不响应未配置来源）；沙箱/联调用测试凭证。
"""
from fastapi import Depends, Query, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api import make_router
from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import (
    ERR_WECHAT_001,
    ERR_WECHAT_002,
    ERR_WECHAT_003,
    ERR_WECHAT_004,
    ERR_WECHAT_005,
    ERR_WECHAT_099,
    ApiError,
)
from app.db.models import User
from app.db.session import SessionLocal, get_db
from app.schemas.common import ApiResponse
from app.schemas.wechat import (
    WechatBindingsOut,
    WechatBindOut,
    WechatBindRequest,
    WechatDeleteOut,
    WechatFindOut,
    WechatUnbindOut,
)
from app.services.wechat.binding import (
    AlreadyBoundToOtherUser,
    list_bindings,
    resolve_user_id,
)
from app.services.wechat.binding import (
    bind as bind_wechat,
)
from app.services.wechat.binding import (
    unbind as unbind_wechat,
)
from app.services.wechat.gateway import handle_message, verify_url
from app.services.wechat.service import find_memories, process_incoming, soft_delete_by_msg

router = make_router(prefix="/api/v1/wechat", tags=["wechat"])


def _wechat_configured() -> bool:
    return bool(settings.wechat_corp_id and settings.wechat_token and settings.wechat_encoding_aes_key)


def _require_configured() -> None:
    if not _wechat_configured():
        raise ApiError(ERR_WECHAT_099, "微信回调未配置（WECHAT_CORP_ID/TOKEN/ENCODING_AES_KEY）", http=503)


class WechatFindRequest(BaseModel):
    """微信"找"请求（S4-02：消息解析 → RAG 搜索）"""

    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(3, ge=1, le=10)


@router.post("/find", response_model=ApiResponse[WechatFindOut])
def wechat_find(
    req: WechatFindRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """微信"找"：沙箱/客户端直接调用（10s/3s 门禁在沙箱可测 WX-007）"""
    result = find_memories(db, user.id, req.query, limit=req.limit)
    return ApiResponse(data=result)


@router.get("/callback")
def wechat_callback_verify(
    msg_signature: str,
    timestamp: str,
    nonce: str,
    echostr: str,
):
    """URL 验证：解密 echostr 原样返回（企微 GET 请求）"""
    _require_configured()
    try:
        plain = verify_url(
            settings.wechat_token, settings.wechat_encoding_aes_key,
            settings.wechat_corp_id, msg_signature, timestamp, nonce, echostr,
        )
    except ValueError as exc:
        raise ApiError(ERR_WECHAT_001, f"URL 验证失败: {exc}", http=403) from exc
    return Response(content=plain, media_type="text/plain")


def _ingest_callback_message(msg: dict) -> dict:
    """在 worker 线程内处理一条回调消息（D09-1 / D09-7）。

    - **自带会话**：不借用请求线程的 Session（`run_in_threadpool` 会在另一线程执行；
      会话跨线程复用是隐性雷），消息处理完即关闭；
    - **D09-1 绑定解析**：用 `gateway` 已解析的 `from_user`（企微客服 openid）
      查 `user_wechat_bindings` → user_id；未绑定 → None（保持"只记录不建内容"语义）。
    """
    with SessionLocal() as db:
        user_id = resolve_user_id(db, msg.get("from_user"))
        if user_id is None:
            # 2026-10-15（台账 §4.20 ㉖）：**未绑定 + 发来绑定码** ⇒ 建立绑定（F6 微信入口的入口）。
            # 绑定成功也不把这条消息当内容 —— 它是"指令"不是记忆，按既有"未绑定只记录"路径落
            # wechat_messages（保 msg_id 幂等 ⇒ 企微重推不会二次消费/二次建绑定）。
            from app.services.wechat.service import bind_by_code

            if bind_by_code(db, msg) is not None:
                return process_incoming(db, msg, user_id=None)
        return process_incoming(db, msg, user_id=user_id)


@router.post("/callback")
async def wechat_callback(
    request: Request,
    msg_signature: str,
    timestamp: str,
    nonce: str,
):
    """收消息：验签→解密→幂等入库→返回 success（企微要求响应"success"或空串）

    D09-7（2026-09-25）：媒体下载是**同步阻塞** HTTP（企微 media/get，超时 30s），
    而本端点是 async —— 原实现直接在事件循环里同步调用 ⇒ 单事件循环（RUNBOOK 铁律
    `--workers 1`）被阻塞期间所有并发回调排队，放大企微 5s 超时重推。
    现把处理体整体 `run_in_threadpool` 出去（含自带 DB 会话）。

    D09-12（2026-09-25）：验签通过但消息缺 `MsgId` 等契约错误此前逃逸为 **500**，
    企微会持续重推同一包；现与验签失败同口径 → **403 WECHAT_002**。
    """
    _require_configured()
    body_str = (await request.body()).decode("utf-8")
    try:
        msg = handle_message(
            settings.wechat_token, settings.wechat_encoding_aes_key,
            settings.wechat_corp_id, msg_signature, timestamp, nonce, body_str,
        )
    except ValueError as exc:
        raise ApiError(ERR_WECHAT_002, f"回调处理失败: {exc}", http=403) from exc
    if msg is not None:
        try:
            await run_in_threadpool(_ingest_callback_message, msg)
        except ValueError as exc:
            raise ApiError(ERR_WECHAT_002, f"回调处理失败: {exc}", http=403) from exc
    return Response(content="success", media_type="text/plain")


@router.post("/bind", response_model=ApiResponse[WechatBindOut])
def wechat_bind(
    req: WechatBindRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """绑定微信渠道标识到当前账号（D09-1 配套：让 F6 主链可被真正打通/验证）

    - 同一 openid 已绑定**他人** → 409 WECHAT_004（默认拒绝换绑，防把别人微信收到的
      记忆改投到我账号）；
    - 同用户重复绑定 → 幂等成功；
    - 渠道白名单见 `WECHAT_BINDING_CHANNELS`。
    """
    try:
        row = bind_wechat(db, user.id, req.openid, req.channel)
    except AlreadyBoundToOtherUser as exc:
        raise ApiError(ERR_WECHAT_004, "该微信已绑定其它账号", http=409) from exc
    except ValueError as exc:
        raise ApiError(ERR_WECHAT_005, str(exc), http=422) from exc
    return ApiResponse(data={
        "bound": True,
        "openid": row.openid,
        "channel": row.channel,
        "user_id": str(row.user_id),
    })


@router.post("/bind-code", response_model=ApiResponse[dict])
def wechat_bind_code(user: User = Depends(get_current_user)):
    """签发**微信绑定码**（2026-10-15 · 台账 §4.20 ㉖）。

    为什么需要它：F6「微信入口」要先知道用户的微信 **openid**，而 openid 只存在企微侧、
    App 无从获取（后端此前也没有任何"绑定码/扫码"流程 ⇒ 功能在产品级不可完成）。
    现流程：App 领码 → 用户在自己的微信里给忆述发「绑定 <码>」→ 回调侧消费码建绑定。

    码**6 位数字、10 分钟有效、单次使用**；同账号重发即作废旧码。
    **只回本次码与有效期**（不返回历史码/剩余次数，最小信息）。
    """
    from app.services.wechat.bind_code import CODE_TTL_SECONDS, issue_code

    code = issue_code(str(user.id))
    return ApiResponse(data={"code": code, "expires_in": CODE_TTL_SECONDS})


@router.get("/bindings", response_model=ApiResponse[WechatBindingsOut])
def wechat_bindings(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """当前账号已绑定的微信渠道列表（不含他人绑定）"""
    rows = list_bindings(db, user.id)
    return ApiResponse(data={"bindings": [
        {
            "openid": r.openid,
            "channel": r.channel,
            "bound_at": r.bound_at.isoformat() if r.bound_at else None,
        }
        for r in rows
    ]})


@router.delete("/bindings", response_model=ApiResponse[WechatUnbindOut])
def wechat_unbind(
    openid: str = Query(..., min_length=1, max_length=128),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """解绑（仅限本人绑定；他人绑定按不存在处理，不泄露信息）"""
    if not unbind_wechat(db, user.id, openid):
        raise ApiError(ERR_WECHAT_003, "绑定不存在", http=404)
    return ApiResponse(data={"unbound": True, "openid": openid})


@router.post("/delete", response_model=ApiResponse[WechatDeleteOut])
def wechat_delete(
    msg_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """微信端软删本条（F6：只删本条；审查 CRITICAL 修复：补鉴权 + 归属校验）"""
    ok = soft_delete_by_msg(db, msg_id, user_id=user.id)
    if not ok:
        raise ApiError(ERR_WECHAT_003, "消息不存在", http=404)
    return ApiResponse(data={"deleted": True, "msg_id": msg_id})
