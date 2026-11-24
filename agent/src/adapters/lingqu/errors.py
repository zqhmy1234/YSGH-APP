"""Normalize provider/LiteLLM failures into a small, stable vocabulary.

Routing decisions must depend on *what* failed, not on the concrete SDK class.
A 400 is a caller bug and must never trigger a fallback; a 429/5xx/timeout is a
provider problem and may fall back to the next profile.
"""

from __future__ import annotations

from .credentials import redact


class LingquError(RuntimeError):
    kind = "provider_error"
    retryable = False

    def __init__(self, detail: str = "", *, status_code: int | None = None) -> None:
        super().__init__(redact(detail))
        self.detail = redact(detail)
        self.status_code = status_code


class AuthError(LingquError):
    kind = "auth"


class BadRequestError(LingquError):
    kind = "bad_request"


class RateLimitError(LingquError):
    kind = "rate_limit"
    retryable = True


class ProviderTimeout(LingquError):
    kind = "timeout"
    retryable = True


class ContextOverflow(LingquError):
    kind = "context_overflow"


class ContentFiltered(LingquError):
    kind = "content_filter"


class ProviderDown(LingquError):
    kind = "provider_down"
    retryable = True


class ProviderInternal(LingquError):
    kind = "provider_internal"
    retryable = True


def _status(exc: object) -> int | None:
    for attr in ("status_code", "code", "http_status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    code = getattr(response, "status_code", None)
    return code if isinstance(code, int) else None


def classify(exc: object) -> LingquError:
    if isinstance(exc, LingquError):
        return exc
    status = _status(exc)
    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if "context" in name or "context" in text or status == 413:
        return ContextOverflow(str(exc), status_code=status)
    if "contentpolicy" in name or "content_filter" in text or "content policy" in text:
        return ContentFiltered(str(exc), status_code=status)
    if "authentication" in name or "permission" in name or status in (401, 403):
        return AuthError(str(exc), status_code=status)
    if "ratelimit" in name or status == 429:
        return RateLimitError(str(exc), status_code=status)
    if "timeout" in name or "timeout" in text:
        return ProviderTimeout(str(exc), status_code=status)
    if status is not None and 500 <= status < 600:
        return ProviderInternal(str(exc), status_code=status)
    if status == 400 or "badrequest" in name or "invalidrequest" in name:
        return BadRequestError(str(exc), status_code=status)
    if "connection" in name or "serviceunavailable" in name or "unreachable" in text:
        return ProviderDown(str(exc), status_code=status)
    return ProviderInternal(str(exc), status_code=status)


def should_fallback(error: LingquError) -> bool:
    return error.retryable
