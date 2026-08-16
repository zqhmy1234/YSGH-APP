"""认证路由（决策 #8：微信授权为主 + 手机号备用 + JWT）"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.core.errors import ApiError
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.db.session import get_db
from app.schemas.auth import (
    PhoneLoginRequest,
    RefreshRequest,
    SendSmsRequest,
    TokenPair,
    UserBrief,
    WechatLoginRequest,
)
from app.schemas.common import ApiResponse

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/wechat", response_model=ApiResponse[TokenPair])
def wechat_login(req: WechatLoginRequest, db: Session = Depends(get_db)):
    """微信登录：code 换 unionid → 建立/获取用户 → 签发 token 对

    Mock 模式（未配置微信密钥或 MOCK_EXTERNAL_AI=true）：
    用 code 本身作为 unionid 的确定性 mock，保证契约消费方可联调。
    """
    if not req.code:
        raise ApiError("AUTH_001", "code 不能为空", http=400)

    # TODO(T1): 调微信接口 code2session 换 openid/unionid（微信登录）
    unionid = f"mock-unionid-{req.code}" if _is_mock() else _wechat_code2session(req.code)

    user = _get_or_create_user(db, unionid=unionid, device_id=req.device_id)
    tokens = _issue_tokens(user["id"], req.device_id)
    return ApiResponse(data=tokens)


@router.post("/phone", response_model=ApiResponse[TokenPair])
def phone_login(req: PhoneLoginRequest, db: Session = Depends(get_db)):
    """手机号验证码登录（备用通道）"""
    # TODO(T1): 校验 sms_codes（防刷：限流+有效期+错误锁定，AUTH-003/004）
    if _is_mock():
        if req.code != "000000":
            raise ApiError("AUTH_003", "验证码错误", http=401)
    else:
        raise ApiError("AUTH_003", "验证码错误", http=401)  # 未接入短信服务前固定拒绝

    user = _get_or_create_user(db, phone=req.phone, device_id="phone-login")
    tokens = _issue_tokens(user["id"], "phone-login")
    return ApiResponse(data=tokens)


@router.post("/sms/send", response_model=ApiResponse[dict])
def send_sms(req: SendSmsRequest, db: Session = Depends(get_db)):
    """发送短信验证码（Mock：直接返回 code 供联调）"""
    # TODO(T1): 阿里云短信（0.045 元/条），限流 + 6 位 + 有效期（AUTH-003）
    if _is_mock():
        return ApiResponse(data={"mock_code": "000000"})
    raise ApiError("AUTH_099", "短信服务未接入", http=501)


@router.post("/refresh", response_model=ApiResponse[TokenPair])
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """refresh token 轮换（AUTH-005：旧 refresh 失效；devices 表可吊销）"""
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        raise ApiError("AUTH_005", "refresh token 无效或过期", http=401) from None

    if payload.get("type") != "refresh":
        raise ApiError("AUTH_005", "token 类型错误", http=401)

    user_id = payload.get("sub")
    device_id = payload.get("device_id", "")
    # TODO(T1): 校验 devices 表 refresh_token 未被吊销（AUTH-006）
    tokens = _issue_tokens(user_id, device_id)
    return ApiResponse(data=tokens)


# ---------- helpers ----------

def _is_mock() -> bool:
    from app.core.config import settings
    return settings.mock_external_ai


def _wechat_code2session(code: str) -> str:
    """TODO(T1): 调微信接口 https://api.weixin.qq.com/sns/jscode2session"""
    raise ApiError("AUTH_099", "微信登录未接入", http=501)


def _get_or_create_user(db: Session, **kwargs) -> dict:
    """TODO(T1): users 表查询/创建（unionid 或 phone 唯一）"""
    from app.db.models import User  # noqa: F401  模型待建（见 sql/schema.sql）
    return {"id": "mock-user-0001", "nickname": "新用户", "avatar": None}


def _issue_tokens(user_id: str, device_id: str) -> TokenPair:
    return TokenPair(
        access_token=create_access_token(user_id, device_id),
        refresh_token=create_refresh_token(user_id, device_id),
        user=UserBrief(id=user_id, nickname="新用户", is_new_user=True),
    )
