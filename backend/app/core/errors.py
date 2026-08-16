"""统一错误码（对齐测试清单 API-007：业务码 + 可读消息 + request_id）"""
from fastapi import Request
from fastapi.responses import JSONResponse


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


def install_error_handlers(app) -> None:
    app.add_exception_handler(ApiError, api_error_handler)
