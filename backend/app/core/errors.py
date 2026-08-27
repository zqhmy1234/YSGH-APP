"""统一错误码（对齐测试清单 API-007：业务码 + 可读消息 + request_id）

P0-7（审查 S3-6）：全仓错误码唯一登记表——code → (语义, http, retryable)。
历史问题：40 个码全部内联在 raise 点、无登记处，导致同码多义（CONTENT_003 敏感 vs
游标、CONTENT_007 413 vs 404）、编号空洞（AUTH_002/006、CLASSIFY_001、EVENT_001-003）。
本表为唯一真源，单测 test_techdebt_p0 校验「码唯一 + http 语义匹配 + raise 处码均在表内」。

使用规则：
  1. 新增错误码必须先在本表登记，再在 raise 处引用常量（禁止裸字符串新码）
  2. raise 处引用 ERR_* 常量：raise ApiError(ERR_CONTENT_003, "...", http=422)
  3. 同码多义拆分已落地（2026-08-26）：
     - CONTENT_003 = 内容含敏感信息(422)；游标错误拆分 CONTENT_008(422)
     - CONTENT_007 = 照片超大小上限(413)；event_items 内容不存在改 EVENT_005(404)
"""
import logging
from dataclasses import dataclass

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

logger = logging.getLogger("yishu.errors")


@dataclass(frozen=True)
class ErrorSpec:
    """错误码登记条目：code 唯一；http 语义；retryable 仅 5xx 类可重试错误为 True"""

    code: str
    message: str
    http: int
    retryable: bool = False


# ---------- 错误码登记表（唯一真源；单测校验唯一性） ----------
_ERROR_SPECS: list[ErrorSpec] = [
    # 认证域
    ErrorSpec("AUTH_001", "认证失败（微信 code 无效 / 用户不存在或已冻结）", 401),
    ErrorSpec("AUTH_003", "验证码错误或已过期", 401),
    ErrorSpec("AUTH_004", "验证码发送过于频繁或达每日上限", 429),
    ErrorSpec("AUTH_005", "token 无效 / 过期 / 已吊销 / 类型错误", 401),
    ErrorSpec("AUTH_099", "认证服务未接入或上游不可用（微信/短信）", 501),
    # ASR 域
    ErrorSpec("ASR_001", "音频参数校验失败", 422),
    ErrorSpec("ASR_002", "ASR 服务不可用（可重试）", 503, retryable=True),
    # 内容域
    ErrorSpec("CONTENT_001", "不支持的 content_type", 422),
    ErrorSpec("CONTENT_002", "重复内容（感知哈希已存在）", 409),
    ErrorSpec("CONTENT_003", "内容含敏感信息未保存", 422),
    ErrorSpec("CONTENT_004", "STS 直传未接入（生产待实现）", 501),
    ErrorSpec("CONTENT_005", "meta 参数非法（JSON/GPS/source 等）", 422),
    ErrorSpec("CONTENT_006", "仅支持照片文件（jpg/png/webp/heic；含魔数校验）", 422),
    ErrorSpec("CONTENT_007", "照片超过大小上限", 413),
    ErrorSpec("CONTENT_008", "游标格式无效（应为 <created_at_iso>|<id>）", 422),
    ErrorSpec("CONTENT_009", "cos_key 非法或不属于当前用户（前缀/对象不存在）", 422),
    # 纠错域
    ErrorSpec("CORR_001", "new_label 非法", 422),
    ErrorSpec("CORR_002", "source 非法", 422),
    ErrorSpec("CORR_003", "任务不存在或已过期", 404),
    ErrorSpec("CORR_004", "任务不属于当前用户（越权查询）", 403),
    # 事件域
    ErrorSpec("EVENT_004", "事件不存在或不属于当前用户", 404),
    ErrorSpec("EVENT_005", "内容不存在或不属于当前用户", 404),
    ErrorSpec("EVENT_006", "事件操作冲突（内容不属于该事件 / 封面不是事件成员）", 409),
    ErrorSpec("EVENT_007", "事件参数非法（拆分内容列表为空等）", 422),
    # 消息域
    ErrorSpec("MSG_001", "status 参数非法", 422),
    ErrorSpec("MSG_002", "消息不存在", 404),
    # 画像级敏感
    ErrorSpec("PROFILE_SENSITIVE_001", "话题参数非法", 422),
    ErrorSpec("PROFILE_SENSITIVE_002", "topic 不能为空", 422),
    ErrorSpec("PROFILE_SENSITIVE_003", "话题不存在", 404),
    # 搜索域
    ErrorSpec("SEARCH_001", "空图片文件", 422),
    ErrorSpec("SEARCH_002", "图片超过大小上限", 422),
    # 同步域
    ErrorSpec("SYNC_001", "limit 参数超出允许范围（≥1 且 ≤单次上限，请按 has_more 分页）", 422),
    # 缩略图域
    ErrorSpec("THUMB_001", "缩略图不可用", 404),
    # 上传域
    ErrorSpec("UPLOAD_001", "init 参数非法", 422),
    ErrorSpec("UPLOAD_002", "上传任务不存在", 404),
    ErrorSpec("UPLOAD_003", "分片参数非法（哈希/越界/超声明大小）", 422),
    ErrorSpec("UPLOAD_004", "complete/建内容参数非法", 422),
    ErrorSpec("UPLOAD_005", "存储后端不支持 STS 临时凭证", 501),
    ErrorSpec("UPLOAD_006", "STS 暂不可用，请走后端中转上传", 503, retryable=True),
    ErrorSpec("UPLOAD_007", "upload_mode 非法", 422),
    ErrorSpec("UPLOAD_008", "STS 直传未接入（生产未配置 COS/STS）", 501),
    ErrorSpec("UPLOAD_009", "上传分片状态冲突（分片未齐 / 同片内容不一致）", 409),
    ErrorSpec("UPLOAD_010", "文件超过后端中转大小上限（>200MB 请走客户端直传）", 413),
    # 微信域
    ErrorSpec("WECHAT_001", "企微 URL 验证失败", 403),
    ErrorSpec("WECHAT_002", "企微回调处理失败", 403),
    ErrorSpec("WECHAT_003", "消息不存在", 404),
    ErrorSpec("WECHAT_099", "微信回调未配置", 503, retryable=True),
    # 分类域
    ErrorSpec("CLASSIFY_002", "任务不存在或已过期", 404),
    ErrorSpec("CLASSIFY_003", "任务不属于当前用户（越权查询）", 403),
]

ERROR_REGISTRY: dict[str, ErrorSpec] = {spec.code: spec for spec in _ERROR_SPECS}


# ---------- 常量（raise 处引用；与登记表一一对应，禁止裸字符串新码） ----------
ERR_AUTH_001 = "AUTH_001"
ERR_AUTH_003 = "AUTH_003"
ERR_AUTH_004 = "AUTH_004"
ERR_AUTH_005 = "AUTH_005"
ERR_AUTH_099 = "AUTH_099"
ERR_ASR_001 = "ASR_001"
ERR_ASR_002 = "ASR_002"
ERR_CONTENT_001 = "CONTENT_001"
ERR_CONTENT_002 = "CONTENT_002"
ERR_CONTENT_003 = "CONTENT_003"
ERR_CONTENT_004 = "CONTENT_004"
ERR_CONTENT_005 = "CONTENT_005"
ERR_CONTENT_006 = "CONTENT_006"
ERR_CONTENT_007 = "CONTENT_007"
ERR_CONTENT_008 = "CONTENT_008"
ERR_CONTENT_009 = "CONTENT_009"
ERR_CORR_001 = "CORR_001"
ERR_CORR_002 = "CORR_002"
ERR_CORR_003 = "CORR_003"
ERR_CORR_004 = "CORR_004"
ERR_EVENT_004 = "EVENT_004"
ERR_EVENT_005 = "EVENT_005"
ERR_EVENT_006 = "EVENT_006"
ERR_EVENT_007 = "EVENT_007"
ERR_MSG_001 = "MSG_001"
ERR_MSG_002 = "MSG_002"
ERR_PROFILE_SENSITIVE_001 = "PROFILE_SENSITIVE_001"
ERR_PROFILE_SENSITIVE_002 = "PROFILE_SENSITIVE_002"
ERR_PROFILE_SENSITIVE_003 = "PROFILE_SENSITIVE_003"
ERR_SEARCH_001 = "SEARCH_001"
ERR_SEARCH_002 = "SEARCH_002"
ERR_SYNC_001 = "SYNC_001"
ERR_THUMB_001 = "THUMB_001"
ERR_UPLOAD_001 = "UPLOAD_001"
ERR_UPLOAD_002 = "UPLOAD_002"
ERR_UPLOAD_003 = "UPLOAD_003"
ERR_UPLOAD_004 = "UPLOAD_004"
ERR_UPLOAD_005 = "UPLOAD_005"
ERR_UPLOAD_006 = "UPLOAD_006"
ERR_UPLOAD_007 = "UPLOAD_007"
ERR_UPLOAD_008 = "UPLOAD_008"
ERR_UPLOAD_009 = "UPLOAD_009"
ERR_UPLOAD_010 = "UPLOAD_010"
ERR_WECHAT_001 = "WECHAT_001"
ERR_WECHAT_002 = "WECHAT_002"
ERR_WECHAT_003 = "WECHAT_003"
ERR_WECHAT_099 = "WECHAT_099"
ERR_CLASSIFY_002 = "CLASSIFY_002"
ERR_CLASSIFY_003 = "CLASSIFY_003"


class ApiError(Exception):
    """业务异常：code 为业务码（如 AUTH_001），http 为 HTTP 状态码"""

    def __init__(self, code: str, message: str, http: int = 400, details: dict | None = None):
        self.code = code
        self.message = message
        self.http = http
        self.details = details
        super().__init__(message)


async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.http,
        content={
            "code": exc.code,
            "message": exc.message,
            "request_id": getattr(request.state, "request_id", ""),
            "details": exc.details,
        },
    )


async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """422 参数校验失败统一信封（P1-A 错误对齐）：
    与业务 ApiError 同构 {code, message, request_id, details}；
    message 取第一个校验错误的人类可读 msg（detail[0].msg → message）。
    """
    errors = exc.errors()
    first = errors[0] if errors else {}
    msg = str(first.get("msg", "请求参数校验失败"))
    return JSONResponse(
        status_code=422,
        content={
            "code": "VALIDATION_ERROR",
            "message": msg,
            "request_id": getattr(request.state, "request_id", ""),
            "details": {"errors": errors},
        },
    )


async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """500 兜底（P1-A 错误对齐）：对外只给脱敏 message（不泄漏堆栈/内部路径/异常对象），
    异常详情走日志/Sentry（request_id 串联，API-008）。"""
    request_id = getattr(request.state, "request_id", "")
    logger.exception("未处理异常 request_id=%s path=%s", request_id, request.url.path)
    return JSONResponse(
        status_code=500,
        content={
            "code": "INTERNAL_ERROR",
            "message": "服务器内部错误，请稍后重试",
            "request_id": request_id,
            "details": None,
        },
    )


def install_error_handlers(app) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unhandled_error_handler)
