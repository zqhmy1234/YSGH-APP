"""认证服务（F2 API→service 下沉：注册/令牌/渠道逻辑）

承载原 api/auth.py 的全部业务逻辑；api 层只留协议层（参数校验 + 调用本服务 + 错误映射）。
行为等价迁移——对外路径 /api/v1/auth/* 与响应结构不变。

- wechat_login / phone_login：渠道 provider 分发（get_login_provider，见 providers.py）
  → 建用户 → 签发 token 对（微信 unionid 三端一致 AUTH-001）
- send_sms：短信发送（get_sms_sender 分发）+ 防刷限流 + 每日上限（AUTH-003/004）
- refresh：refresh token 轮换（B2 R2#7 条件 UPDATE 原子 single-use，AUTH-005/006）

保留成果（以最新 develop 为基准，不得回退）：
  - TD-P3 M6/L2：devices 表只存 refresh_token 哈希 + 明文列迁移期兼容
  - B2 R2#7：refresh 轮换条件 UPDATE（并发双 refresh 携同一旧 token 仅第一个成功）
  - P1-04：并发首登唯一约束冲突 IntegrityError 回滚重查（不再 500）
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.errors import (
    ERR_AUTH_001,
    ERR_AUTH_004,
    ERR_AUTH_005,
    ERR_AUTH_010,
    ApiError,
)
from app.core.security import create_access_token, create_refresh_token, decode_token
from app.db.models import Device, SmsCode, User
from app.schemas.auth import TokenPair, UserBrief
from app.services.auth.providers import (
    _gen_code_salt,
    _gen_sms_code,
    _hash_code,
    _otp_reset,
    _rate_allow,
    get_login_provider,
    get_sms_sender,
)

# send 侧限流配额（TD-P3 M2：防短信轰炸洪泛；与 login 侧配额共同构成双层限流）
_SMS_SEND_IP_LIMIT = 30            # send：同 IP 30 次/分钟
_SMS_SEND_PHONE_LIMIT = 5          # send：同 phone 5 次/分钟（DB 60s/日 10 已有，此为双保险）


def wechat_login(db: Session, req) -> TokenPair:
    """微信登录：code 换 unionid → 建立/获取用户 → 签发 token 对（真实 DB）"""
    provider = get_login_provider("wechat")
    identity = provider.resolve_identity(db, req, client_ip=None)
    return _finish_login(db, identity)


def phone_login(db: Session, req, client_ip: str | None) -> TokenPair:
    """手机号验证码登录（备用通道，真实校验 sms_codes；验证码哈希存储防 DB 泄漏）

    TD-P3 M2（审查中危）：验证码爆破防护（限流/作废/冷却在 providers.py _verify_sms_code）
    """
    provider = get_login_provider("phone")
    identity = provider.resolve_identity(db, req, client_ip=client_ip)
    return _finish_login(db, identity)


def send_sms(db: Session, req, client_ip: str | None) -> dict:
    """发送短信验证码（真实入库，6 位 + 5 分钟有效期 + 防刷限流 + 每日上限）

    TD-P3 M2（审查中危）：send 侧 IP+phone 双层限流（防短信轰炸洪泛）。
    """
    now = datetime.now(timezone.utc)

    # P0-1（审查 H1）：生产环境禁止 mock 验证码直返（任意手机号可接管账户）——
    # 与 wechat_login 的"生产未接入 → 501"对齐；真实短信通道（TODO T1）接入前
    # 生产 phone 登录整体不可用。get_settings 已强制生产 mock_external_ai=False，
    # 此处显式门控双保险（防运行时误改/测试泄漏）。
    if settings.app_env == "production":
        raise ApiError(ERR_AUTH_010, "短信服务未接入（生产环境），请使用微信登录", http=501)

    ip = client_ip or ""
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
    # 安全修复（G1/R6#9）：验证码只存 SHA-256+盐 哈希（DB 泄漏不可直接登录/彩虹表反推），
    # 盐随行落库（sms_codes.salt），校验时按行盐重算比对。
    salt = _gen_code_salt()
    db.add(
        SmsCode(
            phone=req.phone,
            code=_hash_code(code, salt),
            salt=salt,
            expire_at=now + timedelta(minutes=5),
        )
    )
    db.commit()
    # M2：新码发出 → 重置失败计数（每码独立 5 次窗口）
    _otp_reset(req.phone)

    # F8/R1#8：短信发送走 SmsSender 端口分发（mock_external_ai → mock 直返 / 真实通道 501）
    sender = get_sms_sender()
    mock_code = sender.send(req.phone, code)
    if mock_code is None:
        # 真实通道未返回 mock 码（T1 未接入时 AliyunSmsSender 已 501；此处防御兜底）
        raise ApiError(ERR_AUTH_010, "短信服务未接入", http=501)
    return {"mock_code": mock_code}


def refresh(db: Session, req) -> TokenPair:
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
    # TD-P3 M6/L2 + G1/R6#8：DB 泄漏不再可直接复用 30 天会话——
    #   - G1：哈希升级 HMAC-SHA256（独立密钥 refresh_token_hmac_key，与 jwt_secret 隔离），
    #     存储带 `hmac$` 版本前缀；明文列迁移期兼容（refresh_token_hash 为空时回退比对明文）。
    device = db.execute(
        select(Device).where(Device.user_id == user_id, Device.device_id == device_id)
    ).scalar_one_or_none()
    if device is None:
        raise ApiError(ERR_AUTH_005, "refresh token 已吊销", http=401)
    if device.refresh_token_hash:
        valid = _verify_refresh_token_hash(device.refresh_token_hash, req.refresh_token)
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
    return _rotate_refresh_token(db, user, device, device_id, req.refresh_token)


def logout(db: Session, refresh_token: str) -> dict:
    """退出登录（G1/R6#7）：吊销 devices 表该 refresh token 绑定的设备会话（AUTH-006）

    幂等：token 无效 / 过期 / 类型错误 / 设备不存在 → 仍返回 {"ok": True}
    （客户端必清本地凭据；服务端尽力吊销，失败由 refresh 30 天 TTL 兜底）。
    吊销实现：devices 行 refresh_token_hash 与 refresh_token 均置 NULL——
    后续 refresh() 校验落入明文回退分支且明文为空 → 401 已吊销（不再可换新）。
    """
    try:
        payload = decode_token(refresh_token)
    except Exception:
        return {"ok": True}

    if payload.get("type") != "refresh":
        return {"ok": True}
    user_id = payload.get("sub")
    device_id = payload.get("device_id", "")
    if not user_id or not device_id:
        return {"ok": True}

    db.execute(
        update(Device)
        .where(Device.user_id == user_id, Device.device_id == device_id)
        .values(
            refresh_token_hash=None,
            refresh_token=None,
            last_active_at=datetime.now(timezone.utc),
        )
    )
    db.commit()
    return {"ok": True}


# ---------- 注册/令牌公共逻辑 ----------


def _finish_login(db: Session, identity) -> TokenPair:
    """渠道身份 → 建/查用户 → 签发 token 对（渠道公共收口）"""
    user = _get_or_create_user(db, unionid=identity.unionid, phone=identity.phone)
    return _issue_tokens(db, user, identity.device_id, platform=identity.platform)


def _get_or_create_user(db: Session, unionid: str | None = None, phone: str | None = None) -> User:
    """按 unionid 或 phone 查询/创建用户（AUTH-001 三端一致）

    审查修复(P1-04)：并发双端同时首次登录 → 唯一约束冲突（IntegrityError）→
    回滚后重查，不再 500（此前未捕获直接抛 500）。
    """
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
    迁移期兼容（TD-P3 M6 + G1/R6#8）：
      - refresh_token_hash 非空：WHERE 同时匹配 `hmac$` 新哈希与未加前缀的存量 SHA-256
        （一行同一时刻只存一种格式，OR 子句等价命中当前格式，不削弱原子性）
      - refresh_token_hash 为空：WHERE 命中明文（旧行回退比对），登录/轮换即哈希化覆盖。
    IntegrityError 路径保留（并发同设备写竞态兜底，语义同 _issue_tokens）。
    """
    access = create_access_token(user.id, device_id)
    refresh = create_refresh_token(user.id, device_id)
    now = datetime.now(timezone.utc)
    new_hash = _hash_refresh_token(refresh)

    if device.refresh_token_hash:
        old_clause = or_(
            Device.refresh_token_hash == _hash_refresh_token(old_refresh),
            Device.refresh_token_hash == _sha256_legacy(old_refresh),
        )
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


def _hmac_key() -> str:
    """refresh_token 哈希密钥（G1/R6#8：独立于 jwt_secret，函数内读取防导入期快照）"""
    return settings.refresh_token_hmac_key


def _hash_refresh_token(token: str) -> str:
    """refresh token 哈希（G1/R6#8 升级：HMAC-SHA256 + 独立密钥 + 版本前缀）

    - 密钥隔离：refresh_token_hmac_key ≠ jwt_secret——JWT 密钥泄漏无法构造 devices 哈希；
      即使攻击者拿到 DB，也只能离线爆破高熵 JWT（不可行），无法直接复用 30 天会话。
    - 版本前缀 `hmac$`：与 TD-P3 存量无前缀 SHA-256 区分（_verify_refresh_token_hash 双格式兼容）。
    - 存储：devices.refresh_token_hash 只存本函数输出（DB 泄漏不可直接登录）。
    """
    return "hmac$" + hmac.new(
        _hmac_key().encode("utf-8"), token.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _sha256_legacy(token: str) -> str:
    """TD-P3 存量 SHA-256 哈希（迁移期兼容：只用于比对存量行，不用于新写）"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _verify_refresh_token_hash(stored: str, token: str) -> bool:
    """校验存储哈希是否匹配给定 refresh token（secrets.compare_digest 防时序攻击）

    - `hmac$` 前缀 → HMAC-SHA256（G1 现行格式）
    - 无前缀 → TD-P3 存量 SHA-256（迁移期兼容，随后续轮换/登录自动升级）
    """
    if stored.startswith("hmac$"):
        return secrets.compare_digest(stored, _hash_refresh_token(token))
    return secrets.compare_digest(stored, _sha256_legacy(token))


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
