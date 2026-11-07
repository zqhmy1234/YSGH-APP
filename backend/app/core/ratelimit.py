"""通用限流中间件（G1/R6#2/#3，认证安全）

Redis 固定窗口计数（INCR + EXPIRE）按 client_ip / user 维度，先覆盖 auth / ASR(含
guard) / 搜索三域（main.py 已接线；后续域按需在 _DOMAIN_SCOPES 登记）。

设计要点：
  - 域路由：path 前缀 → (scope, ip_limit, user_limit)，不在三域的路径直接放行
  - 键：`yishu:rl:{scope}:ip:{ip}` / `yishu:rl:{scope}:user:{uid}`（Redis 原子窗口）
  - 双维度：client_ip 必查；Authorization Bearer 可解出 user_id 时叠加 user 维度
    （token 缺失/无效仅 IP 维度——避免把坏 token 请求放成不设防）
  - 白名单：settings.rate_limit_whitelist（逗号分隔 IP）直接放行；
    rate_limit_trust_proxy=True 时优先 X-Forwarded-For 首值（仅可信反代后开启）
  - 降级：Redis 不可用 → 进程内 MemoryRateLimitStore 继续限流（不 500，日志告警），
    满足「降级不 500」验收；单副本部署语义足够，多副本登记：换 Redis 键即可
  - request_id 链路：本中间件置于 RequestIDMiddleware 内侧（main.py 中 RequestID 最外层），
    429 响应同样带 X-Request-ID，不破坏 API-008 全链路日志串联
"""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque

from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.core.config import settings
from app.core.security import decode_token

logger = logging.getLogger("yishu.ratelimit")

# 429 响应业务码（通用限流；errors.py 登记表按"认证/业务域"约束，此处独立域码）
RATE_LIMIT_CODE = "RATE_LIMITED"
_RATE_LIMIT_PREFIX = "yishu:rl:"

# 域路由登记：path 前缀 → scope（scope 名对应 settings.rate_limit_{scope}_{ip,user}）
# 仅覆盖 auth / asr(含 guard) / search 三域（先覆盖三域，其余域按需新增登记）
_DOMAIN_SCOPES: tuple[tuple[str, str], ...] = (
    ("/api/v1/auth", "auth"),
    ("/api/v1/asr", "asr"),
    ("/api/v1/guard", "asr"),   # 护栏独立前缀，归 ASR 域同一配额（B5b 高频护栏调用）
    ("/api/v1/search", "search"),
)


# ---------------------------------------------------------------------------
# 存储抽象（可测：Redis 生产 / Memory 降级与单测）
# ---------------------------------------------------------------------------


class RateLimitStore:
    """限流计数存储：allow(key, limit, window) → 窗口内是否放行"""

    def allow(self, key: str, limit: int, window: float) -> bool:
        raise NotImplementedError


class RedisRateLimitStore(RateLimitStore):
    """Redis 固定窗口：INCR 计数 + EXPIRE 窗口（单条 PIPELINE 原子执行）

    Redis 连接不可用（连接被拒/超时）→ 抛 RedisError，由中间件降级到 MemoryStore。
    """

    def __init__(self) -> None:
        from redis import Redis

        self._redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry_on_timeout=False,
        )

    def allow(self, key: str, limit: int, window: float) -> bool:
        pipe = self._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, int(window))
        count, _ = pipe.execute()
        return int(count) <= limit


class MemoryRateLimitStore(RateLimitStore):
    """进程内滑动窗口计数（Redis 降级 / 单进程测试用；线程安全）

    多副本部署登记：多副本各持有独立计数 → 上限按副本数放大；生产多副本请确保
    Redis 可用（降级仅作为兜底，不承诺严格全局限流）。
    """

    def __init__(self) -> None:
        self._buckets: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets[key]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            bucket.append(now)
            return len(bucket) <= limit


# 模块级当前存储（holder dict 避免 ruff PLW0603 global；测试经 set_store 注入）
_store: dict[str, RateLimitStore | None] = {"store": None}


def get_store() -> RateLimitStore:
    """当前限流存储（惰性初始化 Redis；测试可经 set_store 注入）"""
    store = _store["store"]
    if store is None:
        store = RedisRateLimitStore()
        _store["store"] = store
    return store


def set_store(store: RateLimitStore | None) -> None:
    """注入/重置限流存储（None → 下次惰性重建；供测试与降级用）"""
    _store["store"] = store


def _degrade_to_memory(exc: Exception) -> RateLimitStore:
    """Redis 故障 → 降级进程内 MemoryStore（不 500；日志告警便于运维接入）"""
    logger.warning("限流 Redis 不可用，降级进程内 MemoryStore: %s", exc)
    store = MemoryRateLimitStore()
    set_store(store)
    return store


# ---------------------------------------------------------------------------
# 中间件
# ---------------------------------------------------------------------------


def _scope_for_path(path: str) -> str | None:
    """按 path 前缀识别限流域；不在三域 → None（放行）"""
    for prefix, scope in _DOMAIN_SCOPES:
        if path == prefix or path.startswith(prefix + "/"):
            return scope
    return None


def _whitelisted_ips() -> set[str]:
    raw = (settings.rate_limit_whitelist or "").strip()
    if not raw:
        return set()
    return {ip.strip() for ip in raw.split(",") if ip.strip()}


def _client_ip(request: Request) -> str:
    """客户端 IP：trust_proxy 时优先 X-Forwarded-For 首值（仅可信反代后开启）"""
    if settings.rate_limit_trust_proxy:
        xff = request.headers.get("X-Forwarded-For")
        if xff:
            return xff.split(",")[0].strip()
    return request.client.host if request.client else ""


def _bearer_user_id(request: Request) -> str | None:
    """从 Authorization Bearer 解出 user_id（失败/缺失 → None，仅 IP 维度）"""
    auth = request.headers.get("Authorization") or ""
    if not auth.startswith("Bearer "):
        return None
    try:
        payload = decode_token(auth[len("Bearer "):].strip())
    except Exception:  # noqa: BLE001 —— 坏 token 不阻断限流（仅降为 IP 维度）
        return None
    sub = payload.get("sub")
    return str(sub) if sub else None


def _ip_limit(scope: str) -> int:
    return int(getattr(settings, f"rate_limit_{scope}_ip", 0) or 0)


def _user_limit(scope: str) -> int:
    return int(getattr(settings, f"rate_limit_{scope}_user", 0) or 0)


def _check_all(request: Request, scope: str, ip: str, uid: str | None) -> str | None:
    """按 IP / user 双维度检查；返回命中的超限维度（"ip"/"user"），放行返回 None。

    Redis 故障自动降级 MemoryStore 后重查一次（降级不 500）。
    """
    window = float(settings.rate_limit_window or 60)

    def _allow(store: RateLimitStore, key: str, limit: int) -> bool:
        try:
            return store.allow(key, limit, window)
        except Exception as exc:  # noqa: BLE001 —— Redis 故障降级，不 500
            return _degrade_to_memory(exc).allow(key, limit, window)

    if not _allow(get_store(), f"{_RATE_LIMIT_PREFIX}{scope}:ip:{ip}", _ip_limit(scope)):
        return "ip"
    if uid and not _allow(get_store(), f"{_RATE_LIMIT_PREFIX}{scope}:user:{uid}", _user_limit(scope)):
        return "user"
    return None


def _rate_limited(request: Request, scope: str, dimension: str) -> JSONResponse:
    """429 统一信封（含 request_id——RequestID 中间件在外层已注入 request.state）"""
    return JSONResponse(
        status_code=429,
        content={
            "code": RATE_LIMIT_CODE,
            "message": "请求过于频繁，请稍后再试",
            "request_id": getattr(request.state, "request_id", ""),
            "details": {"scope": scope, "dimension": dimension},
        },
        headers={"Retry-After": str(settings.rate_limit_window or 60)},
    )


class RateLimitMiddleware(BaseHTTPMiddleware):
    """通用限流（auth/ASR/搜索三域；白名单直通；Redis 故障降级不 500）"""

    async def dispatch(self, request: Request, call_next):
        scope = _scope_for_path(request.url.path)
        if scope is None or not settings.rate_limit_enabled:
            return await call_next(request)
        ip = _client_ip(request)
        if ip in _whitelisted_ips():
            return await call_next(request)

        uid = _bearer_user_id(request)
        # 存储为同步 Redis 调用 → 移出事件循环（不阻塞其他请求）
        try:
            rejected = await run_in_threadpool(_check_all, request, scope, ip, uid)
        except Exception:  # noqa: BLE001 —— 兜底：限流失败不拖垮业务
            logger.exception("限流检查异常（放行）: path=%s", request.url.path)
            return await call_next(request)
        if rejected is not None:
            return _rate_limited(request, scope, rejected)
        return await call_next(request)
