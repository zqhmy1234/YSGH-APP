"""认证路由（决策 #8：微信授权为主 + 手机号备用 + JWT）

真实 DB 接入（S1-02）：
- users：unionid/phone 查询与创建（AUTH-001 unionid 三端一致）
- devices：refresh_token 记录 + 吊销校验（AUTH-005/006）
- sms_codes：验证码生成/校验/防刷（AUTH-003/004）

微信 code2session（Wave4-L）：配置 WECHAT_APPID/WECHAT_SECRET 后走真实
jscode2session（优先 unionid、回退 openid）；未配置时 dev/test 走 mock、production
保持 501（不静默降级 mock 登录）。
"""
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
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

    if _wechat_configured():
        unionid = _wechat_code2session(req.code)
    elif settings.app_env == "production":
        # 安全修复（审查 CRITICAL）：生产环境未接入微信时拒绝登录，
        # 不允许任意 code 创建 mock 用户（认证形同虚设）
        raise ApiError("AUTH_099", "微信登录未接入", http=501)
    else:
        # 仅开发/测试环境允许 mock（联调用）
        unionid = f"mock-unionid-{req.code}"

    user = _get_or_create_user(db, unionid=unionid)
    tokens = _issue_tokens(db, user, req.device_id)
    return ApiResponse(data=tokens)


@router.post("/phone", response_model=ApiResponse[TokenPair])
def phone_login(req: PhoneLoginRequest, db: Session = Depends(get_db)):
    """手机号验证码登录（备用通道，真实校验 sms_codes；验证码哈希存储防 DB 泄漏）"""
    now = datetime.now(timezone.utc)
    record = db.execute(
        select(SmsCode).where(
            SmsCode.phone == req.phone,
            SmsCode.used_at.is_(None),
            SmsCode.expire_at > now,
        ).order_by(SmsCode.id.desc()).limit(1)
    ).scalar_one_or_none()

    # 哈希比较（secrets.compare_digest 防时序攻击；修复：原明文比较）
    if record is None or not secrets.compare_digest(_hash_code(req.code), record.code or ""):
        raise ApiError("AUTH_003", "验证码错误或已过期", http=401)

    record.used_at = now
    db.commit()

    user = _get_or_create_user(db, phone=req.phone)
    tokens = _issue_tokens(db, user, "phone-login", platform="phone")
    return ApiResponse(data=tokens)


@router.post("/sms/send", response_model=ApiResponse[dict])
def send_sms(req: SendSmsRequest, db: Session = Depends(get_db)):
    """发送短信验证码（真实入库，6 位 + 5 分钟有效期 + 防刷限流 + 每日上限）"""
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

    # 每日上限（安全修复：防短信轰炸，10 条/日）
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.scalar(
        select(func.count()).select_from(SmsCode).where(
            SmsCode.phone == req.phone,
            SmsCode.created_at >= day_start,
        )
    ) or 0
    if today_count >= 10:
        raise ApiError("AUTH_004", "今日验证码发送次数已达上限", http=429)

    code = _gen_sms_code()
    # 安全修复：验证码只存 SHA-256 哈希（DB 泄漏不可直接登录）
    db.add(SmsCode(phone=req.phone, code=_hash_code(code), expire_at=now + timedelta(minutes=5)))
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

# 微信开放平台 jscode2session（决策 #8 微信登录；Wave4-L 接入，替换 mock unionid）
# 文档：https://developers.weixin.qq.com/miniprogram/dev/OpenApiDoc/user-login/code2Session.html
WECHAT_CODE2SESSION_URL = "https://api.weixin.qq.com/sns/jscode2session"


def _wechat_configured() -> bool:
    """微信登录配置就绪判定（appid/secret 齐全才走真实 code2session）"""
    return bool(settings.wechat_appid and settings.wechat_secret)


def _wechat_code2session(code: str) -> str:
    """调微信开放平台 code2session 换 openid/unionid（AUTH-001 unionid 三端一致）

    GET {url}?appid=..&secret=..&js_code=..&grant_type=authorization_code
    成功：{openid, session_key, [unionid]}——已绑定微信开放平台才返回 unionid，
    优先用 unionid（三端一致主键），未绑定回退 openid（保证登录可用）。
    失败语义（不静默降级 mock）：
      - 业务错误（errcode≠0，如 40029 code 无效/已过期、40163 已使用）→ 401 AUTH_001
      - 上游网络/HTTP 异常 → 502 AUTH_099
    """
    import httpx

    try:
        resp = httpx.get(
            WECHAT_CODE2SESSION_URL,
            params={
                "appid": settings.wechat_appid,
                "secret": settings.wechat_secret,
                "js_code": code,
                "grant_type": "authorization_code",
            },
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise ApiError("AUTH_099", "微信登录服务不可用", http=502) from exc

    if data.get("errcode"):
        raise ApiError(
            "AUTH_001", f"微信 code 无效或已过期: {data.get('errmsg')}", http=401
        )
    unionid = data.get("unionid") or data.get("openid")
    if not unionid:
        raise ApiError("AUTH_099", "微信登录响应缺少 openid/unionid", http=502)
    return str(unionid)


def _get_or_create_user(db: Session, unionid: str | None = None, phone: str | None = None) -> User:
    """按 unionid 或 phone 查询/创建用户（AUTH-001 三端一致）

    审查修复(P1-04)：并发双端同时首次登录 → 唯一约束冲突（IntegrityError）→
    回滚后重查，不再 500（此前未捕获直接抛 500）。
    """
    from sqlalchemy.exc import IntegrityError

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
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            # 并发下另一请求已创建 → 重查
            if unionid:
                user = db.execute(select(User).where(User.unionid == unionid)).scalar_one_or_none()
            elif phone:
                user = db.execute(select(User).where(User.phone == phone)).scalar_one_or_none()
            if user is None:
                raise
        db.refresh(user)
    return user


def _issue_tokens(db: Session, user: User, device_id: str, platform: str = "android") -> TokenPair:
    """签发 token 对 + 记录/更新 devices 表（refresh 可吊销）

    修复（审查 MINOR）：platform 参数化（原硬编码 android），
    微信登录默认 android（客户端后续补 platform 字段时透传）。
    """
    access = create_access_token(user.id, device_id)
    refresh = create_refresh_token(user.id, device_id)

    device = db.execute(
        select(Device).where(Device.user_id == user.id, Device.device_id == device_id)
    ).scalar_one_or_none()
    if device is None:
        device = Device(user_id=user.id, device_id=device_id, platform=platform)
        db.add(device)
    device.refresh_token = refresh
    device.last_active_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        # 并发同设备登录（客户端双 ensureLogin 竞态，2026-08-24 真机实测）：
        # 唯一约束 uq_devices_user_device 冲突 → 回滚重查复用已有行
        db.rollback()
        existing = db.execute(
            select(Device).where(Device.user_id == user.id, Device.device_id == device_id)
        ).scalar_one_or_none()
        if existing is None:
            raise
        existing.refresh_token = refresh
        existing.last_active_at = datetime.now(timezone.utc)
        db.commit()
        device = existing

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
    return f"{secrets.randbelow(1000000):06d}"


def _hash_code(code: str) -> str:
    """验证码哈希（SHA-256；仅存哈希，校验时重算比对，DB 泄漏不可登录）"""
    import hashlib

    return hashlib.sha256(code.encode("utf-8")).hexdigest()
