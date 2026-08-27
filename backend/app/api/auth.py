"""认证路由（协议层：参数校验 + 调用 service + 错误映射）

业务逻辑已下沉 backend/app/services/auth/（F2 API→service 下沉，P0-5 auth 先行）：
  - services/auth/auth.py      —— 认证服务（注册/令牌/渠道编排）
  - services/auth/providers.py —— 登录渠道策略（LoginProvider：wechat/phone/sms-mock）
                                  + 短信发送端口（SmsSender）
本层只保留：路由定义 + schema 参数校验（FastAPI 依赖注入）+ 服务调用 + 统一响应封装；
业务错误由服务层抛 ApiError、全局 error handler 映射（对外路径与响应结构不变，行为等价）。

微信 code2session（Wave4-L）：配置 WECHAT_APPID/WECHAT_SECRET 后走真实
jscode2session（优先 unionid、回退 openid）；未配置时 dev/test 走 mock、production
保持 501（不静默降级 mock 登录）。
"""
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.auth import (
    LogoutRequest,
    PhoneLoginRequest,
    RefreshRequest,
    SendSmsRequest,
    TokenPair,
    WechatLoginRequest,
)
from app.schemas.common import ApiResponse
from app.services.auth import auth as auth_service

# 兼容既有引用（test_auth_db R2#7 直接从 app.api.auth 导入）：
# `_hash_refresh_token`/`_rotate_refresh_token` 定义于服务层 auth.py，此处 re-export 保持导入路径不变。
from app.services.auth.auth import _hash_refresh_token, _rotate_refresh_token  # noqa: F401

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


@router.post("/wechat", response_model=ApiResponse[TokenPair])
def wechat_login(req: WechatLoginRequest, db: Session = Depends(get_db)):
    """微信登录：code 换 unionid → 建立/获取用户 → 签发 token 对（真实 DB）"""
    return ApiResponse(data=auth_service.wechat_login(db=db, req=req))


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
    return ApiResponse(data=auth_service.phone_login(db=db, req=req, client_ip=_client_ip(request)))


@router.post("/sms/send", response_model=ApiResponse[dict])
def send_sms(req: SendSmsRequest, request: Request, db: Session = Depends(get_db)):
    """发送短信验证码（真实入库，6 位 + 5 分钟有效期 + 防刷限流 + 每日上限）

    TD-P3 M2（审查中危）：send 侧 IP+phone 双层限流（防短信轰炸洪泛）。
    """
    return ApiResponse(data=auth_service.send_sms(db=db, req=req, client_ip=_client_ip(request)))


@router.post("/refresh", response_model=ApiResponse[TokenPair])
def refresh(req: RefreshRequest, db: Session = Depends(get_db)):
    """refresh token 轮换（AUTH-005：旧 refresh 失效；devices 表可吊销 AUTH-006）"""
    return ApiResponse(data=auth_service.refresh(db=db, req=req))


@router.post("/logout", response_model=ApiResponse[dict])
def logout(req: LogoutRequest, db: Session = Depends(get_db)):
    """退出登录（G1/R6#7）：吊销该 refresh token 绑定的设备会话（AUTH-006）

    幂等：token 无效/过期/设备不存在 → 仍返回 ok（客户端必清本地凭据）。
    """
    return ApiResponse(data=auth_service.logout(db=db, refresh_token=req.refresh_token))


def _client_ip(request: Request) -> str:
    """协议层：提取客户端 IP 传给服务层（限流键用）"""
    return request.client.host if request.client else ""
