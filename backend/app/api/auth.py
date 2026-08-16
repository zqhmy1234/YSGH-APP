"""认证路由（决策 #8：微信授权为主 + 手机号备用 + JWT）

真实 DB 接入（S1-02）：
- users：unionid/phone 查询与创建（AUTH-001 unionid 三端一致）
- devices：refresh_token 记录 + 吊销校验（AUTH-005/006）
- sms_codes：验证码生成/校验/防刷（AUTH-003/004）

微信 code2session 仍未接入真实密钥时走 mock（MOCK_EXTERNAL_AI 或未配微信密钥）。
"""
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import ApiError
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.db.models import Device, SmsCode, User
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
    """微信登录：code 换 unionid → 建立/获取用户 → 签发 token 对（真实 DB）"""
    if not req.code:
        raise ApiError("AUTH_001", "code 不能为空", http=400)

    unionid = _wechat_code2session(req.code) if _wechat_configured() else f"mock-unionid-{req.code}"

    user = _get_or_create_user(db, unionid=unionid)
    tokens = _issue_tokens(db, user, req.device_id)
    return ApiResponse(data=tokens)


@router.post("/phone", response_model=ApiResponse[TokenPair])
def phone_login(req: PhoneLoginRequest, db: Session = Depends(get_db)):
    """手机号验证码登录（备用通道，真实校验 sms_codes）"""
    now = datetime.now(timezone.utc)
    record = db.execute(
        select(SmsCode).where(
            SmsCode.phone == req.phone,
            SmsCode.used_at.is_(None),
            SmsCode.expire_at > now,
        ).order_by(SmsCode.id.desc()).limit(1)
    ).scalar_one_or_none()

    if record is None or record.code != req.code:
        raise ApiError("AUTH_003", "验证码错误或已过期", http=401)

    record.used_at = now
    db.commit()

    user = _get_or_create_user(db, phone=req.phone)
    tokens = _issue_tokens(db, user, "phone-login")
    return ApiResponse(data=tokens)


@router.post("/sms/send", response_model=ApiResponse[dict])
def send_sms(req: SendSmsRequest, db: Session = Depends(get_db)):
    """发送短信验证码（真实入库，6 位 + 5 分钟有效期 + 防刷限流）"""
    now = datetime.now(timezone.utc)

    # 防刷（AUTH-004）：同一手机号 60s 内已有未使用验证码 → 拒绝重发
    recent = db.execute(
        select(SmsCode).where(
            SmsCode.phone == req.phone,
            SmsCode.used_at.is_(None),
            SmsCode.created_at > now - timedelta(seconds=60),
        ).limit(1)
    ).scalar_one_or_none()
    if recent is not None:
        raise ApiError("AUTH_004", "验证码发送过于频繁，请稍后再试", http=429)

    code = _gen_sms_code()
    db.add(SmsCode(phone=req.phone, code=code, expire_at=now + timedelta(minutes=5)))
    db.commit()

    if settings.mock_external_ai:
        # mock 模式：直接返回验证码供联调（生产走阿里云短信 0.045 元/条）
        return ApiResponse(data={"mock_code": code})
    # TODO(T1): 接入阿里云短信发送
    raise ApiError("AUTH_099", "短信服务未接入", http=501)


@router.post("/refresh", response_model=ApiResponse[TokenPair])
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """refresh token 轮换（AUTH-005：旧 refresh 失效；devices 表可吊销 AUTH-006）"""
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        raise ApiError("AUTH_005", "refresh token 无效或过期", http=401) from None

    if payload.get("type") != "refresh":
        raise ApiError("AUTH_005", "token 类型错误", http=401)

    user_id = payload.get("sub")
    device_id = payload.get("device_id", "")

    # 吊销校验：devices 表中存储的 refresh_token 必须匹配（AUTH-006 退出/改密后失效）
    device = db.execute(
        select(Device).where(Device.user_id == user_id, Device.device_id == device_id)
    ).scalar_one_or_none()
    if device is None or device.refresh_token != req.refresh_token:
        raise ApiError("AUTH_005", "refresh token 已吊销", http=401)

    user = db.get(User, user_id)
    if user is None or user.status != 1:
        raise ApiError("AUTH_001", "用户不存在或已冻结", http=401)

    tokens = _issue_tokens(db, user, device_id)
    return ApiResponse(data=tokens)


# ---------- helpers ----------

def _wechat_configured() -> bool:
    """微信登录配置就绪判定（appid/secret 未配置时走 mock）"""
    # TODO(T1): 配置 WECHAT_APPID/WECHAT_SECRET 后走真实 code2session
    return False


def _wechat_code2session(code: str) -> str:
    """TODO(T1): 调微信接口 https://api.weixin.qq.com/sns/jscode2session 换 openid/unionid"""
    raise ApiError("AUTH_099", "微信登录未接入", http=501)


def _get_or_create_user(db: Session, unionid: str | None = None, phone: str | None = None) -> User:
    """按 unionid 或 phone 查询/创建用户（AUTH-001 三端一致）"""
    user = None
    if unionid:
        user = db.execute(select(User).where(User.unionid == unionid)).scalar_one_or_none()
    elif phone:
        user = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()

    if user is None:
        user = User(
            id=str(uuid.uuid4()),
            unionid=unionid,
            phone=phone,
            nickname="新用户",
            status=1,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    return user


def _issue_tokens(db: Session, user: User, device_id: str) -> TokenPair:
    """签发 token 对 + 记录/更新 devices 表（refresh 可吊销）"""
    access = create_access_token(user.id, device_id)
    refresh = create_refresh_token(user.id, device_id)

    device = db.execute(
        select(Device).where(Device.user_id == user.id, Device.device_id == device_id)
    ).scalar_one_or_none()
    if device is None:
        device = Device(user_id=user.id, device_id=device_id, platform="android")
        db.add(device)
    device.refresh_token = refresh
    device.last_active_at = datetime.now(timezone.utc)
    db.commit()

    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        user=UserBrief(
            id=user.id,
            nickname=user.nickname,
            avatar=user.avatar,
            is_new_user=False,
        ),
    )


def _gen_sms_code() -> str:
    import secrets

    return f"{secrets.randbelow(1000000):06d}"
