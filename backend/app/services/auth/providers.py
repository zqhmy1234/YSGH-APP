"""认证服务 · 登录渠道策略（F8/R1#8）

渠道抽象（行为等价迁移，源自 api/auth.py）：
  - LoginProvider ABC：把一次登录请求解析为稳定用户身份（AuthIdentity）
    · WechatLoginProvider   —— 微信渠道（code → code2session → unionid；配置/生产/mock 三态）
    · PhoneLoginProvider    —— 真实手机号渠道（短信验证码校验：限流 + OTP + 原子消费）
    · SmsMockLoginProvider  —— sms-mock 渠道（dev/test 联调：验证码经 MockSmsSender 直返）
  - SmsSender 端口：短信发送抽象
    · MockSmsSender    —— mock 直返验证码（联调用，零费用）
    · AliyunSmsSender  —— 真实通道占位（TODO T1 接入前 501，与 P0-1 生产门控一致）
  - get_login_provider / get_sms_sender 按配置分发（真实消费方见 auth.auth 服务层）

保留成果（以最新 develop 为基准，不得回退）：
  - TD-P3 M2（审查中危）：验证码爆破防护（内存滑动窗口 + 冷却）+ 验证码 SHA-256 哈希落库
  - B2 R2#8：验证码原子消费（条件 UPDATE single-use，并发同码双登录仅一成功）
"""
from __future__ import annotations

import hashlib
import secrets
import threading
import time
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_003,
    ERR_AUTH_004,
    ERR_AUTH_099,
    ApiError,
)
from app.db.models import SmsCode

# ---------------------------------------------------------------------------
# 短信发送端口（SmsSender）
# ---------------------------------------------------------------------------


class SmsSender(ABC):
    """短信验证码发送端口：生产接真实短信网关，mock 直返验证码供联调。

    send() 返回可供直返的验证码字符串（仅 mock 通道），真实通道发送后返回 None
    （验证码不进入响应，防任意手机号接管——P0-1）。
    """

    name: str = ""

    @abstractmethod
    def send(self, phone: str, code: str) -> str | None:
        """发送验证码；返回可直返的验证码（mock），或 None（真实通道不返回）。"""


class MockSmsSender(SmsSender):
    """sms-mock 通道：验证码直返（dev/test 联调用，零费用）"""

    name = "sms-mock"

    def send(self, phone: str, code: str) -> str | None:
        return code


class AliyunSmsSender(SmsSender):
    """真实短信通道占位：TODO(T1) 接入阿里云短信（0.045 元/条）。

    接入前保持 501（与 wechat_login 的"生产未接入 → 501"对齐：真实通道未接入
    时明确失败，不静默降级 mock——任意手机号可接管账户的认证绕过）。
    """

    name = "aliyun"

    def send(self, phone: str, code: str) -> str | None:
        raise ApiError(ERR_AUTH_099, "短信服务未接入", http=501)


def get_sms_sender() -> SmsSender:
    """按配置选择短信发送通道：
    mock_external_ai=true → mock（验证码直返）；否则真实通道（T1 未接入 → 501）。"""
    return MockSmsSender() if settings.mock_external_ai else AliyunSmsSender()


# ---------------------------------------------------------------------------
# 登录渠道策略（LoginProvider）
# ---------------------------------------------------------------------------


@dataclass
class AuthIdentity:
    """登录渠道解析出的稳定用户身份（供注册/令牌逻辑建用户）"""

    unionid: str | None = None
    phone: str | None = None
    device_id: str = "phone-login"
    platform: str = "android"


class LoginProvider(ABC):
    """登录渠道策略：把一次登录请求解析为稳定身份。

    失败抛 ApiError（由全局 error handler 映射错误响应）；校验通过返回 AuthIdentity。
    协议层不感知渠道细节（真实消费方：auth.auth 的 wechat_login/phone_login）。
    """

    name: str = ""

    @abstractmethod
    def resolve_identity(self, db: Session, req, client_ip: str | None) -> AuthIdentity:
        """渠道特有登录校验 → 稳定身份（用户创建/签发令牌由服务层统一完成）"""


# ---------------- 手机号渠道基础设施（send 与 login 共用） ----------------
# TD-P3 M2 修复（审查中危）：验证码爆破防护
# 轻量内存计数（单进程）：
#   - 每 phone 每窗口失败 ≥_OTP_MAX_FAILS 次 → 作废当前验证码 + 冷却（防爆破）
#   - send / login 均做 IP+phone 双层滑动窗口限流（防短信轰炸 / 登录尝试洪泛）
# 多副本部署登记：需将 _RATE/_OTP_STATE 换成 Redis 计数（INCR + EXPIRE，键名同名），
# 本实现保留单进程语义，生产单副本即可覆盖 MVP。
_OTP_WINDOW_SECONDS = 600          # 失败计数窗口 / 冷却时长（10 分钟）
_OTP_MAX_FAILS = 5                 # 每码窗口内失败 ≥5 次作废
_RATE_WINDOW = 60                  # 限流窗口（秒）
_LOGIN_IP_LIMIT = 60               # login：同 IP 60 次/分钟
_LOGIN_PHONE_LIMIT = 10            # login：同 phone 10 次/分钟

_RATE_LOCK = threading.Lock()
_RATE: dict[str, deque] = defaultdict(deque)          # key -> 时间戳队列
_OTP_STATE: dict[str, dict] = {}                       # phone -> {fails, window_start, cooldown_until}


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


def _hash_code(code: str, salt: str | None = None) -> str:
    """验证码哈希（G1/R6#9：SHA-256 + 每码随机盐；DB 泄漏不可彩虹表反推）

    - salt 非空：sha256(f"{salt}:{code}")（当前写入格式；salt 随行落库）
    - salt 为空：sha256(code)（TD-P3 存量未加盐记录的比对兼容）
    """
    if salt:
        return hashlib.sha256(f"{salt}:{code}".encode()).hexdigest()
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _gen_code_salt() -> str:
    """每码随机盐（16 hex 字符 = 64 bit 熵；随 sms_codes.salt 落库）"""
    return secrets.token_hex(8)


def _gen_sms_code() -> str:
    return f"{secrets.randbelow(1000000):06d}"


def _verify_sms_code(db: Session, req, client_ip: str | None) -> str:
    """手机号验证码校验（Phone/SmsMock 渠道共用）→ 返回校验通过的手机号。

    限流 → 哈希比对 → 失败计数/作废/冷却 → 原子消费（B2 R2#8 竞态修复）：
      并发同码双登录仅一个成功——UPDATE sms_codes SET used_at WHERE id=:id AND used_at IS NULL
      先提交者 rowcount=1 消费成功，后者 rowcount=0（该码已被消费/作废 → 401）。
    """
    ip = client_ip or ""
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
    # G1/R6#9：加盐——按记录行 salt 重算（存量无 salt 行走无盐 SHA-256 兼容分支）
    if record is None or not secrets.compare_digest(
        _hash_code(req.code, record.salt), record.code or ""
    ):
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
    return req.phone


class PhoneLoginProvider(LoginProvider):
    """真实手机号渠道：短信验证码登录（真实网关 T1 接入后生产走此通道）"""

    name = "phone"

    def resolve_identity(self, db: Session, req, client_ip: str | None = None) -> AuthIdentity:
        phone = _verify_sms_code(db, req, client_ip)
        return AuthIdentity(phone=phone, device_id="phone-login", platform="phone")


class SmsMockLoginProvider(LoginProvider):
    """sms-mock 渠道（dev/test 联调）：验证码经 MockSmsSender 直返，登录校验语义与 phone 一致。

    与 PhoneLoginProvider 共享 _verify_sms_code（同库校验，行为等价）；
    独立命名以表达渠道身份——真实短信通道接入后两渠道可在发送/校验侧独立演化。
    """

    name = "sms-mock"

    def resolve_identity(self, db: Session, req, client_ip: str | None = None) -> AuthIdentity:
        phone = _verify_sms_code(db, req, client_ip)
        return AuthIdentity(phone=phone, device_id="phone-login", platform="phone")


# ---------------- 微信渠道 ----------------

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


class WechatLoginProvider(LoginProvider):
    """微信渠道：code 换 unionid → 建立/获取用户 → 签发 token 对（真实 DB）"""

    name = "wechat"

    def resolve_identity(self, db: Session, req, client_ip: str | None = None) -> AuthIdentity:
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

        return AuthIdentity(unionid=unionid, device_id=req.device_id, platform="android")


def get_login_provider(channel: str) -> LoginProvider:
    """按配置选择登录渠道 provider（F8/R1#8 分发；真实消费方见 auth.auth 服务层）

    - "wechat"    → 微信渠道（WechatLoginProvider）
    - "phone"     → 手机号渠道；mock_external_ai=true 时短信通道退化为 sms-mock
    - "sms-mock"  → 显式指定 mock 渠道（dev/test 联调）
    """
    if channel == "wechat":
        return WechatLoginProvider()
    if channel == "phone":
        return SmsMockLoginProvider() if settings.mock_external_ai else PhoneLoginProvider()
    if channel == "sms-mock":
        return SmsMockLoginProvider()
    raise ValueError(f"未知登录渠道: {channel!r}")
