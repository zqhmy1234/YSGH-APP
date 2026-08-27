"""api 包：路由 + 共享基础设施

R4#14（对齐 API-008）：RequestIdRoute 在成功响应体回填 request_id——
ApiResponse.request_id 默认空串，由路由把实际 request_id 注入；错误响应
（api_error_handler/validation_error_handler）已有 request_id 值 → 不覆盖。
所有 api 模块用 make_router() 建 router（等价 APIRouter + route_class=RequestIdRoute），
新增端点自动获得回填，无需逐端点接线。
"""
import json

from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute


class RequestIdRoute(APIRoute):
    """成功响应体回填 request_id（API-008 全链路日志可按 request_id 串联）

    仅处理 JSONResponse 且 body 为含"空 request_id 字段"的 dict（即 ApiResponse 成功体）；
    错误响应 request_id 已有值 → 不覆盖；FileResponse / 纯文本响应不动。
    """

    def get_route_handler(self):
        original = super().get_route_handler()

        async def custom_handler(request: Request) -> Response:
            response = await original(request)
            request_id = getattr(request.state, "request_id", "")
            if request_id and isinstance(response, JSONResponse):
                try:
                    body = json.loads(response.body)
                except (ValueError, TypeError):
                    return response
                if (
                    isinstance(body, dict)
                    and isinstance(body.get("request_id"), str)
                    and not body["request_id"]
                ):
                    body["request_id"] = request_id
                    payload = json.dumps(
                        body, ensure_ascii=False, separators=(",", ":")
                    ).encode("utf-8")
                    response.body = payload
                    response.headers["content-length"] = str(len(payload))
            return response

        return custom_handler


def make_router(*args, **kwargs):
    """建带 RequestIdRoute 的 APIRouter（R4#14 成功响应 request_id 回填）"""
    return APIRouter(*args, route_class=RequestIdRoute, **kwargs)
