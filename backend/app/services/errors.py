"""服务层细粒度异常（R4#3 · 重构侦察 API 契约 R4-P1#3）

背景：服务层此前用 ValueError/KeyError 二元异常表达全部失败，api 层只能全量映射
（events 域 ValueError→404、upload 域 ValueError→422/KeyError→404），
404/409/413/422 四类语义错位（UPLOAD_004 同时承载"参数非法"与"分片未齐"）。

本模块为服务层唯一异常出口，api 层按子类型映射 HTTP：
  NotFoundError   → 404（资源不存在或非本人，IDOR 不泄露存在性）
  ConflictError   → 409（状态冲突：内容不属于事件 / 分片未齐 / 同片异内容）
  ValidationError → 422（参数/状态校验失败）
  TooLargeError   → 413（超过大小上限：>200MB 请走客户端直传）

兼容性：NotFoundError 同时是 KeyError 与 ValueError 的子类（既有调用方与测试
pytest.raises(KeyError)/pytest.raises(ValueError) 仍可捕获）；其余为 ValueError
子类。api 层按子类型先行精确映射（顺序：NotFound → Conflict → TooLarge →
Validation/ValueError 兜底），消息统一取 .message（规避 KeyError 的引号 str）。
"""
from __future__ import annotations


class ServiceError(Exception):
    """服务层异常基类：携带可读 message，api 层据此构造 ApiError。"""

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message)


class NotFoundError(ServiceError, KeyError, ValueError):
    """资源不存在或非本人（→ 404）：KeyError+ValueError 双兼容既有调用方与测试。"""


class ConflictError(ServiceError, ValueError):
    """状态冲突（→ 409）：内容不属于事件 / 分片未齐 / 同片内容不一致。"""


class ValidationError(ServiceError, ValueError):
    """参数或状态校验失败（→ 422）。"""


class TooLargeError(ServiceError, ValueError):
    """超过大小上限（→ 413）。"""
