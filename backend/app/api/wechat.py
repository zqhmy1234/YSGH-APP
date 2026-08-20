"""企微客服回调 API（F6 微信入口 · S4-01/02）

GET  /api/v1/wechat/callback —— URL 验证（echostr 原样返回，企微确认归属）
POST /api/v1/wechat/callback —— 收消息（验签→解密→幂等入库→"success"）
POST /api/v1/wechat/find      —— 微信"找"（S4-02：消息解析→RAG 搜索→回复，沙箱可测）
POST /api/v1/wechat/delete  —— 微信端软删本条（msg_id）

未配置 WECHAT_* 时回调一律拒绝（安全：不响应未配置来源）；沙箱/联调用测试凭证。
"""
from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.config import settings
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.services.wechat.gateway import handle_message, verify_url
from app.services.wechat.service import find_memories, process_incoming, soft_delete_by_msg

router = APIRouter(prefix="/api/v1/wechat", tags=["wechat"])


def _wechat_configured() -> bool:
    return bool(settings.wechat_corp_id and settings.wechat_token and settings.wechat_encoding_aes_key)


def _require_configured() -> None:
    if not _wechat_configured():
        raise ApiError("WECHAT_099", "微信回调未配置（WECHAT_CORP_ID/TOKEN/ENCODING_AES_KEY）", http=503)


class WechatFindRequest(BaseModel):
    """微信"找"请求（S4-02：消息解析 → RAG 搜索）"""

    query: str = Field(..., min_length=1, max_length=200)
    limit: int = Field(3, ge=1, le=10)


@router.post("/find", response_model=ApiResponse[dict])
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
        raise ApiError("WECHAT_001", f"URL 验证失败: {exc}", http=403) from exc
    return Response(content=plain, media_type="text/plain")


@router.post("/callback")
async def wechat_callback(
    request: Request,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    db: Session = Depends(get_db),
):
    """收消息：验签→解密→幂等入库→返回 success（企微要求响应"success"或空串）"""
    _require_configured()
    body_str = (await request.body()).decode("utf-8")
    try:
        msg = handle_message(
            settings.wechat_token, settings.wechat_encoding_aes_key,
            settings.wechat_corp_id, msg_signature, timestamp, nonce, body_str,
        )
    except ValueError as exc:
        raise ApiError("WECHAT_002", f"回调处理失败: {exc}", http=403) from exc
    if msg is not None:
        process_incoming(db, msg)
    return Response(content="success", media_type="text/plain")


@router.post("/delete", response_model=ApiResponse)
def wechat_delete(
    msg_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """微信端软删本条（F6：只删本条；审查 CRITICAL 修复：补鉴权 + 归属校验）"""
    ok = soft_delete_by_msg(db, msg_id, user_id=user.id)
    if not ok:
        raise ApiError("WECHAT_003", "消息不存在", http=404)
    return ApiResponse(data={"deleted": True, "msg_id": msg_id})
