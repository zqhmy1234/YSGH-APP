"""认证服务包（F2 API→service 下沉 + F8 登录渠道策略抽象）

- auth.auth      —— 认证服务：注册/令牌/渠道编排（wechat_login/phone_login/send_sms/refresh）
- auth.providers —— 登录渠道策略：LoginProvider（wechat/phone/sms-mock）+ SmsSender 端口
"""
from app.services.auth.auth import (
    _hash_refresh_token,
    _issue_tokens,
    _rotate_refresh_token,
    logout,
    phone_login,
    refresh,
    send_sms,
    wechat_login,
)
from app.services.auth.providers import (
    AliyunSmsSender,
    AuthIdentity,
    LoginProvider,
    MockSmsSender,
    PhoneLoginProvider,
    SmsMockLoginProvider,
    SmsSender,
    WechatLoginProvider,
    get_login_provider,
    get_sms_sender,
)

__all__ = [
    # 服务层入口（api 协议层调用）
    "wechat_login",
    "phone_login",
    "send_sms",
    "refresh",
    "logout",
    # 令牌公共逻辑（test_auth_db R2#7 经 app.api.auth re-export 引用）
    "_hash_refresh_token",
    "_issue_tokens",
    "_rotate_refresh_token",
    # 登录渠道策略
    "LoginProvider",
    "AuthIdentity",
    "WechatLoginProvider",
    "PhoneLoginProvider",
    "SmsMockLoginProvider",
    "get_login_provider",
    # 短信发送端口
    "SmsSender",
    "MockSmsSender",
    "AliyunSmsSender",
    "get_sms_sender",
]
