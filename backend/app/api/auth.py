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
import threading
import time
import uuid
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_003,
    ERR_AUTH_004,
    ERR_AUTH_005,
    ERR_AUTH_099,
    ApiError,
)
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

# ---- TD-P3 M2 修复（审查中危）：验证码爆破防护 ----
# 轻量内存计数（单进程）：
#   - 每 phone 每窗口失败 ≥_OTP_MAX_FAILS 次 → 作废当前验证码 + 冷却（防爆破）
#   - send / login 均做 IP+phone 双层滑动窗口限流（防短信轰炸 / 登录尝试洪泛）
# 多副本部署登记：需将 _RATE/_OTP_STATE 换成 Redis 计数（INCR + EXPIRE，键名同名），
# 本实现保留单进程语义，生产单副本即可覆盖 MVP。
_OTP_WINDOW_SECONDS = 600          # 失败计数窗口 / 冷却时长（10 分钟）
_OTP_MAX_FAILS = 5                 # 每码窗口内失败 ≥5 次作废
_RATE_WINDOW = 60                  # 限流窗口（秒）
_SMS_SEND_IP_LIMIT = 30            # send：同 IP 30 次/分钟
_SMS_SEND_PHONE_LIMIT = 5          # send：同 phone 5 次/分钟（DB 60s/日 10 已有，此为双保险）
_LOGIN_IP_LIMIT = 60               # login：同 IP 60 次/分钟
_LOGIN_PHONE_LIMIT = 10            # login：同 phone 10 次/分钟

_RATE_LOCK = threading.Lock()
_RATE: dict[str, deque] = defaultdict(deque)          # key -> 时间戳队列
_OTP_STATE: dict[str, dict] = {}                       # phone -> {fails, window_start, cooldown_until}


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else ""


def _rate_allow(key: str, limit: int, window: float = _RATE_WINDOW) -> bool:
    """滑动窗口限流：window 秒内 ≤ limit 次放行，否则拒绝"""
    now = time.monotonic()
    with _RATE_LOCK:
        bucket = _RATE[key]
        while bucket and now - bucket[0] >= window:
            bucket.popleft()
        bucket.append(now)
        return len(bucket) <= limit


def _otp_fail(phone: str) -> bool:
    """记录一次失败；返回是否已达作废阈值（≥_OTP_MAX_FAILS）"""
    now = time.monotonic()
    with _RATE_LOCK:
        st = _OTP_STATE.get(phone)
        if st is None or now - st["window_start"] >= _OTP_WINDOW_SECONDS:
            st = {"fails": 0, "window_start": now, "cooldown_until": 0.0}
            _OTP_STATE[phone] = st
        st["fails"] += 1
        return st["fails"] >= _OTP_MAX_FAILS


def _otp_start_cooldown(phone: str) -> None:
    with _RATE_LOCK:
        st = _OTP_STATE.get(phone)
        if st is None:
            st = {"fails": _OTP_MAX_FAILS, "window_start": time.monotonic(), "cooldown_until": 0.0}
            _OTP_STATE[phone] = st
        st["cooldown_until"] = time.monotonic() + _OTP_WINDOW_SECONDS


def _otp_in_cooldown(phone: str) -> bool:
    now = time.monotonic()
    with _RATE_LOCK:
        st = _OTP_STATE.get(phone)
        return st is not None and now < st["cooldown_until"]


def _otp_reset(phone: str) -> None:
    """登录成功 / 新码发出 → 清除失败计数（每码独立窗口）"""
    with _RATE_LOCK:
        _OTP_STATE.pop(phone, None)


@router.post("/wechat", response_model=ApiResponse[TokenPair])
def wechat_login(req: WechatLoginRequest, db: Session = Depends(get_db)):
    """微信登录：code 换 unionid → 建立/获取用户 → 签发 token 对（真实 DB）"""
    if not req.code:
        raise ApiError(ERR_AUTH_001, "code 不能为空", http=400)

    if _wechat_configured():
        unionid = _wechat_code2session(req.code)
    elif settings.app_env == "production":
        # 安全修复（审查 CRITICAL）：生产环境未接入微信时拒绝登录，
        # 不允许任意 code 创建 mock 用户（认证形同虚设）
        raise ApiError(ERR_AUTH_099, "微信登录未接入", http=501)
    else:
        # 仅开发/测试环境允许 mock（联调用）
        unionid = f"mock-unionid-{req.code}"

    user = _get_or_create_user(db, unionid=unionid)
    tokens = _issue_tokens(db, user, req.device_id)
    return ApiResponse(data=tokens)


@router.post("/phone", response_model=ApiResponse[TokenPair])
def phone_login(
    req: PhoneLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
):
    """手机号验证码登录（备用通道，真实校验 sms_codes；验证码哈希存储防 DB 泄漏）

    TD-P3 M2（审查中危）：验证码爆破防护
      - IP+phone 双层滑动窗口限流（超限 429）
      - 每 phone 每窗口失败 ≥5 次 → 作废当前验证码（used_at 置位）+ 冷却 10 分钟
    """
    ip = _client_ip(request)
    if not _rate_allow(f"login:ip:{ip}", _LOGIN_IP_LIMIT):
        raise ApiError(ERR_AUTH_004, "登录尝试过于频繁，请稍后再试", http=429)
    if not _rate_allow(f"login:phone:{req.phone}", _LOGIN_PHONE_LIMIT):
        raise ApiError(ERR_AUTH_004, "登录尝试过于频繁，请稍后再试", http=429)
    if _otp_in_cooldown(req.phone):
        raise ApiError(ERR_AUTH_004, "验证码错误次数过多，请稍后再试", http=429)

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
        # M2：失败计数 → 达阈值作废该码并冷却（防 6 位码 5 分钟窗口内爆破）
        if _otp_fail(req.phone):
            if record is not None and record.used_at is None:
                record.used_at = now
                db.commit()
            _otp_start_cooldown(req.phone)
            raise ApiError(
                ERR_AUTH_004, "验证码错误次数过多，该验证码已作废，请重新获取", http=429
            )
        raise ApiError(ERR_AUTH_003, "验证码错误或已过期", http=401)

    _otp_reset(req.phone)
    # R2#8 竞态修复：验证码原子消费——并发同码双登录仅一个成功。
    # 原实现：SELECT 未用码 → 校验 → record.used_at 置位 + commit，存在竞态窗口
    # （两会话都读到未用码、都校验通过 → 同码双登录）。
    # 现改 UPDATE sms_codes SET used_at=now() WHERE id=:id AND used_at IS NULL：
    # rowcount=0 → 该码已被并发请求消费/作废 → 401。
    consumed = db.execute(
        update(SmsCode)
        .where(SmsCode.id == record.id, SmsCode.used_at.is_(None))
        .values(used_at=now)
    )
    if consumed.rowcount == 0:
        db.rollback()
        raise ApiError(ERR_AUTH_003, "验证码已失效，请重新获取", http=401)
    db.commit()

    user = _get_or_create_user(db, phone=req.phone)
    tokens = _issue_tokens(db, user, "phone-login", platform="phone")
    return ApiResponse(data=tokens)


@router.post("/sms/send", response_model=ApiResponse[dict])
def send_sms(req: SendSmsRequest, request: Request, db: Session = Depends(get_db)):
    """发送短信验证码（真实入库，6 位 + 5 分钟有效期 + 防刷限流 + 每日上限）

    TD-P3 M2（审查中危）：send 侧 IP+phone 双层限流（防短信轰炸洪泛）。
    """
    now = datetime.now(timezone.utc)

    # P0-1（审查 H1）：生产环境禁止 mock 验证码直返（任意手机号可接管账户）——
    # 与 wechat_login 的"生产未接入 → 501"对齐；真实短信通道（TODO T1）接入前
    # 生产 phone 登录整体不可用。get_settings 已强制生产 mock_external_ai=False，
    # 此处显式门控双保险（防运行时误改/测试泄漏）。
    if settings.app_env == "production":
        raise ApiError(ERR_AUTH_099, "短信服务未接入（生产环境），请使用微信登录", http=501)

    ip = _client_ip(request)
    if not _rate_allow(f"sms:ip:{ip}", _SMS_SEND_IP_LIMIT):
        raise ApiError(ERR_AUTH_004, "发送过于频繁，请稍后再试", http=429)
    if not _rate_allow(f"sms:phone:{req.phone}", _SMS_SEND_PHONE_LIMIT):
        raise ApiError(ERR_AUTH_004, "发送过于频繁，请稍后再试", http=429)

    # 防刷（AUTH-004）：同一手机号 60s 内已有未使用验证码 → 拒绝重发
    recent = db.execute(
        select(SmsCode).where(
            SmsCode.phone == req.phone,
            SmsCode.used_at.is_(None),
            SmsCode.created_at > now - timedelta(seconds=60),
        ).limit(1)
    ).scalar_one_or_none()
    if recent is not None:
        raise ApiError(ERR_AUTH_004, "验证码发送过于频繁，请稍后再试", http=429)

    # 每日上限（安全修复：防短信轰炸，10 条/日）
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_count = db.scalar(
        select(func.count()).select_from(SmsCode).where(
            SmsCode.phone == req.phone,
            SmsCode.created_at >= day_start,
        )
    ) or 0
    if today_count >= 10:
        raise ApiError(ERR_AUTH_004, "今日验证码发送次数已达上限", http=429)

    code = _gen_sms_code()
    # 安全修复：验证码只存 SHA-256 哈希（DB 泄漏不可直接登录）
    db.add(SmsCode(phone=req.phone, code=_hash_code(code), expire_at=now + timedelta(minutes=5)))
    db.commit()
    # M2：新码发出 → 重置失败计数（每码独立 5 次窗口）
    _otp_reset(req.phone)

    if settings.mock_external_ai:
        # mock 模式：直接返回验证码供联调（生产走阿里云短信 0.045 元/条）
        return ApiResponse(data={"mock_code": code})
    # TODO(T1): 接入阿里云短信发送
    raise ApiError(ERR_AUTH_099, "短信服务未接入", http=501)


@router.post("/refresh", response_model=ApiResponse[TokenPair])
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """refresh token 轮换（AUTH-005：旧 refresh 失效；devices 表可吊销 AUTH-006）"""
    try:
        payload = decode_token(req.refresh_token)
    except Exception:
        raise ApiError(ERR_AUTH_005, "refresh token 无效或过期", http=401) from None

    if payload.get("type") != "refresh":
        raise ApiError(ERR_AUTH_005, "token 类型错误", http=401)

    user_id = payload.get("sub")
    device_id = payload.get("device_id", "")

    # 吊销校验：devices 表存 refresh_token 哈希，比对哈希（AUTH-006 退出/改密后失效）
    # TD-P3 M6/L2（审查中危/低危）：DB 泄漏不再可直接复用 30 天会话——
    # 明文列迁移期兼容（refresh_token_hash 为空时回退比对明文，随后续登录哈希化覆盖）。
    device = db.execute(
        select(Device).where(Device.user_id == user_id, Device.device_id == device_id)
    ).scalar_one_or_none()
    if device is None:
        raise ApiError(ERR_AUTH_005, "refresh token 已吊销", http=401)
    if device.refresh_token_hash:
        valid = secrets.compare_digest(device.refresh_token_hash, _hash_refresh_token(req.refresh_token))
    else:
        valid = bool(device.refresh_token) and secrets.compare_digest(
            device.refresh_token, req.refresh_token
        )
    if not valid:
        raise ApiError(ERR_AUTH_005, "refresh token 已吊销", http=401)

    user = db.get(User, user_id)
    if user is None or user.status != 1:
        raise ApiError(ERR_AUTH_001, "用户不存在或已冻结", http=401)

    # R2#7 竞态修复：轮换改条件 UPDATE（原子 single-use），不再 _issue_tokens 读-改-写——
    # 并发双 refresh 携同一旧 token 只有第一个成功，第二个 rowcount=0 → 401（重放窗口消除）
    tokens = _rotate_refresh_token(db, user, device, device_id, req.refresh_token)
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
        raise ApiError(ERR_AUTH_099, "微信登录服务不可用", http=502) from exc

    if data.get("errcode"):
        raise ApiError(
            ERR_AUTH_001, f"微信 code 无效或已过期: {data.get('errmsg')}", http=401
        )
    unionid = data.get("unionid") or data.get("openid")
    if not unionid:
        raise ApiError(ERR_AUTH_099, "微信登录响应缺少 openid/unionid", http=502)
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
    _store_refresh_token(device, refresh)
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
        _store_refresh_token(existing, refresh)
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


def _rotate_refresh_token(
    db: Session,
    user: User,
    device: Device,
    device_id: str,
    old_refresh: str,
) -> TokenPair:
    """refresh token 轮换（R2#7 竞态修复）：条件 UPDATE 原子 single-use

    原实现 SELECT 校验 → _issue_tokens 读-改-写：并发双 refresh 携同一旧 token
    都在校验后读旧值写新值 → 旧 token 出现重放窗口、双会话并存。
    现改为条件 UPDATE（WHERE 命中旧 token 才写新值）：
      - 第一个请求 rowcount=1 轮换成功
      - 第二个请求 WHERE 已不命中（旧 token 已被换掉）→ rowcount=0 → 401 已吊销
    迁移期兼容（TD-P3 M6）：refresh_token_hash 为空的行按明文比对（WHERE 命中明文）；
    IntegrityError 路径保留（并发同设备写竞态兜底，语义同 _issue_tokens）。
    """
    access = create_access_token(user.id, device_id)
    refresh = create_refresh_token(user.id, device_id)
    now = datetime.now(timezone.utc)
    new_hash = _hash_refresh_token(refresh)

    if device.refresh_token_hash:
        old_clause = Device.refresh_token_hash == _hash_refresh_token(old_refresh)
    else:
        # 迁移期明文行：WHERE 命中明文才轮换（哈希化后由上一分支接管）
        old_clause = (Device.refresh_token == old_refresh) & (
            Device.refresh_token.isnot(None)
        )

    stmt = (
        update(Device)
        .where(Device.user_id == user.id, Device.device_id == device_id, old_clause)
        .values(
            refresh_token_hash=new_hash,
            refresh_token=None,
            refresh_rotated_at=now,
            last_active_at=now,
        )
    )
    try:
        result = db.execute(stmt)
        if result.rowcount == 0:
            db.rollback()
            raise ApiError(ERR_AUTH_005, "refresh token 已吊销", http=401)
        db.commit()
    except IntegrityError:
        # 并发同设备写竞态（保留原 _issue_tokens 的 IntegrityError 路径语义）
        db.rollback()
        raise
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


def _hash_refresh_token(token: str) -> str:
    """refresh token 哈希（SHA-256；devices 表只存哈希，DB 泄漏不可直接复用 30 天会话）"""
    import hashlib

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _store_refresh_token(device: Device, refresh: str) -> None:
    """TD-P3 M6（审查中危/低危）：devices 表只存 refresh_token 哈希 + 最后轮换时间

    - refresh_token 明文列写入后清空（迁移期遗留明文行由 refresh() 回退兼容）
    - refresh_rotated_at 记录最后轮换时间（可观测 / 后续按需做过期策略）
    """
    now = datetime.now(timezone.utc)
    device.refresh_token_hash = _hash_refresh_token(refresh)
    device.refresh_token = None
    device.refresh_rotated_at = now
    device.last_active_at = now
