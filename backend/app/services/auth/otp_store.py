"""验证码爆破防护/登录限流的计数后端（P2-2 · 2026-09-10 迁 Redis）

背景（2026-09-10 安全深扫 P2-2）：原实现为 providers.py 模块级内存
（_RATE deque / _OTP_STATE dict），单进程语义——多 uvicorn worker/多副本部署下
每进程各计各的，爆破阈值实际放大 N 倍（providers 旧注释自认「生产单副本即可覆盖」）。
本模块把计数后端化，与 app/core/ratelimit.py 同构：

  - RedisOtpStore（生产默认）：滑动窗口=ZSET、失败计数=INCR+EXPIRE(nx)（窗口从
    首击起算，等价原 window_start 语义）、冷却=SET EX（键自动过期，顺带根治原
    _OTP_STATE 只 pop 不清过期项的轻微内存驻留）。
  - MemoryOtpStore：原内存实现逐字搬入（保留语义=降级兜底 + 测试注入用）。
  - Redis 故障 → logger.warning + 降级 Memory 并粘住（与 ratelimit._degrade_to_memory
    同款：不因限流后端把登录打成 500；单副本降级语义仍正确，多副本降级=登记风险）。
  - 测试：conftest autouse 注入 Memory（与微信凭证沙箱 fixture 同族纪律——测试
    零 Redis 写依赖、行为与迁移前逐字一致）；Redis 真路径由
    tests/test_otp_store_redis.py（integration）钉桩。

键前缀 yishu:otp:*，与限流中间件 yishu:rl:* 分域不互扰。
"""
from __future__ import annotations

import logging
import secrets
import threading
import time
from collections import defaultdict, deque

logger = logging.getLogger("yishu.otp_store")


class OtpStore:
    """接口：五方法与 providers 原私有函数语义一一对应（行为全等红线）"""

    def rate_allow(self, key: str, limit: int, window: float) -> bool:
        """滑动窗口：window 秒内 ≤ limit 次放行（含本次记入）"""
        raise NotImplementedError

    def otp_fail(self, phone: str, window: float, max_fails: int) -> bool:
        """记一次失败；返回是否已达作废阈值（≥max_fails）"""
        raise NotImplementedError

    def otp_start_cooldown(self, phone: str, ttl: float) -> None:
        raise NotImplementedError

    def otp_in_cooldown(self, phone: str) -> bool:
        raise NotImplementedError

    def otp_reset(self, phone: str) -> None:
        """登录成功/新码发出 → 清失败计数（冷却一并清，对齐原 pop 整条 state）"""
        raise NotImplementedError


class MemoryOtpStore(OtpStore):
    """原 providers.py 内存实现逐字搬入（降级兜底/测试注入）；线程安全同前"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._rate: dict[str, deque] = defaultdict(deque)
        self._otp: dict[str, dict] = {}

    def rate_allow(self, key: str, limit: int, window: float) -> bool:
        now = time.monotonic()
        with self._lock:
            bucket = self._rate[key]
            while bucket and now - bucket[0] >= window:
                bucket.popleft()
            bucket.append(now)
            return len(bucket) <= limit

    def otp_fail(self, phone: str, window: float, max_fails: int) -> bool:
        now = time.monotonic()
        with self._lock:
            st = self._otp.get(phone)
            if st is None or now - st["window_start"] >= window:
                st = {"fails": 0, "window_start": now}
                self._otp[phone] = st
            st["fails"] += 1
            return st["fails"] >= max_fails

    def otp_start_cooldown(self, phone: str, ttl: float) -> None:
        now = time.monotonic()
        with self._lock:
            st = self._otp.get(phone)
            if st is None:
                st = {"fails": 0, "window_start": now}
                self._otp[phone] = st
            st["cooldown_until"] = now + ttl

    def otp_in_cooldown(self, phone: str) -> bool:
        now = time.monotonic()
        with self._lock:
            st = self._otp.get(phone)
            return st is not None and now < st.get("cooldown_until", 0.0)

    def otp_reset(self, phone: str) -> None:
        with self._lock:
            self._otp.pop(phone, None)


class RedisOtpStore(OtpStore):
    """Redis 后端：跨进程/跨副本共享计数（多 worker 下阈值不再放大）

    时钟用 time.time()（epoch 秒，跨进程可比）；键全部带 TTL，无驻留。
    连接池懒建；任何 Redis 异常由 get_backend() 侧捕获降级，本类不吞错。
    """

    _PREFIX = "yishu:otp"

    def __init__(self) -> None:
        from redis import Redis

        from app.core.config import settings

        self._redis = Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
            retry_on_timeout=False,
        )

    # ---- 内部键 ----
    def _rate_key(self, key: str) -> str:
        return f"{self._PREFIX}:rate:{key}"

    def _fails_key(self, phone: str) -> str:
        return f"{self._PREFIX}:fails:{phone}"

    def _cool_key(self, phone: str) -> str:
        return f"{self._PREFIX}:cool:{phone}"

    def rate_allow(self, key: str, limit: int, window: float) -> bool:
        now = time.time()
        k = self._rate_key(key)
        pipe = self._redis.pipeline(transaction=True)
        pipe.zremrangebyscore(k, 0, now - window)   # 逐出窗口外旧击
        pipe.zadd(k, {f"{now}:{secrets.token_hex(4)}": now})  # member 唯一防同刻合并
        pipe.expire(k, int(window) + 1)            # 空闲键自清
        pipe.zcard(k)
        _, _, _, count = pipe.execute()
        return int(count) <= limit

    def otp_fail(self, phone: str, window: float, max_fails: int) -> bool:
        k = self._fails_key(phone)
        pipe = self._redis.pipeline(transaction=True)
        pipe.incr(k)
        # expire nx：仅无 TTL（窗口首击）时设过期 → 窗口从第一击起算，
        # 等价原 window_start 语义（每击顺延=放松防护，绝不做）
        pipe.expire(k, int(window), nx=True)
        fails, _ = pipe.execute()
        return int(fails) >= max_fails

    def otp_start_cooldown(self, phone: str, ttl: float) -> None:
        # 覆盖式续冷（对齐原实现每次达阈值重设 cooldown_until）
        self._redis.set(self._cool_key(phone), "1", ex=int(ttl))

    def otp_in_cooldown(self, phone: str) -> bool:
        return bool(self._redis.exists(self._cool_key(phone)))

    def otp_reset(self, phone: str) -> None:
        self._redis.delete(self._fails_key(phone), self._cool_key(phone))


# ---------------------------------------------------------------------------
# 后端选择：惰性 Redis、故障降级 Memory 并粘住（与 ratelimit 同构）
# holder dict 避免 ruff PLW0603 global；测试经 set_backend 注入
# ---------------------------------------------------------------------------

_backend: dict[str, OtpStore | None] = {"store": None, "degraded": False}


def get_backend() -> OtpStore:
    store = _backend["store"]
    if store is not None:
        return store
    try:
        store = RedisOtpStore()
        store.otp_in_cooldown("__probe__")  # 建连探活（exists 最廉价只读）
    except Exception as exc:  # noqa: BLE001 —— Redis 不可用不 500，降级内存（多副本下阈值放大=已知登记风险）
        logger.warning("OTP 计数 Redis 不可用，降级进程内 Memory（多副本部署时阈值按副本数放大）: %s", exc)
        store = MemoryOtpStore()
        _backend["degraded"] = True
    _backend["store"] = store
    return store


def set_backend(store: OtpStore | None) -> None:
    """注入/重置后端（None → 下次惰性重建；conftest 测试沙箱与运维切换用）"""
    _backend["store"] = store
    _backend["degraded"] = False


def is_degraded() -> bool:
    """当前是否处于 Redis 降级态（运维观测/测试断言用）"""
    get_backend()
    return _backend["degraded"]
