"""otp_store Redis 真路径钉桩（P2-2 · 2026-09-10 迁 Redis 验收）

全仓 autouse 把后端注入为 Memory —— 本文件显式换 RedisOtpStore 打真容器
（yishu-redis localhost:6379/0），键带随机后缀用例自清，验证四件事：
①rate_allow ZSET 滑窗计数语义（=limit 放行 / limit+1 拒）；②otp_fail INCR
+EXPIRE(nx) 阈值与窗口 TTL（首击起算不顺延）；③cooldown SET EX 生效+ttl；
④reset 双键清。另钉「多进程共享」的本质收益：两个独立 store 实例（模拟两
worker）计数互通——这正是内存版做不到的。

Redis 不可用（容器没起）→ 整文件 skip（显式原因，不静默假绿）。
"""
import time
import uuid

import pytest

pytestmark = pytest.mark.integration

pytest.importorskip("redis")


def _fresh_store():
    from app.services.auth.otp_store import RedisOtpStore

    try:
        store = RedisOtpStore()
        store._redis.ping()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"yishu-redis 容器不可用（docker ps 检查）：{exc}")
    return store


@pytest.fixture()
def store():
    s = _fresh_store()
    yield s


def _tag() -> str:
    return uuid.uuid4().hex[:8]


def test_rate_allow_window_limit(store):
    k = f"t:rate:{_tag()}"
    for i in range(3):
        assert store.rate_allow(k, 3, 60) is True, f"第 {i + 1} 击应放行"
    assert store.rate_allow(k, 3, 60) is False, "第 4 击应拒"
    store._redis.delete(k)


def test_fail_threshold_and_shared_across_instances(store):
    """阈值达标 + 跨实例共享（模拟多 worker——内存版的核心缺陷点）"""
    phone = f"139{_tag()}"
    s2 = _fresh_store()  # 第二个独立实例=另一进程视角
    assert store.otp_fail(phone, 600, 5) is False
    assert s2.otp_fail(phone, 600, 5) is False, "第二实例应看到第一实例的计数（共享 Redis）"
    for _ in range(3):
        hit = store.otp_fail(phone, 600, 5)
    assert hit is True, "第 5 次失败应达阈值"
    fails_key = store._fails_key(phone)
    ttl = store._redis.ttl(fails_key)
    assert 0 < ttl <= 600, f"失败计数键应带窗口 TTL，实测 {ttl}"
    store._redis.delete(fails_key)


def test_cooldown_set_ttl_and_reset(store):
    phone = f"138{_tag()}"
    assert store.otp_in_cooldown(phone) is False
    store.otp_start_cooldown(phone, 2)
    assert store.otp_in_cooldown(phone) is True
    assert store._redis.ttl(store._cool_key(phone)) in (1, 2)
    store.otp_reset(phone)
    assert store.otp_in_cooldown(phone) is False, "reset 应连冷却一并清"


def test_fail_window_not_extended_per_hit(store):
    """EXPIRE nx：后续失败不顺延窗口（防「每击续命」放松爆破防护）"""
    phone = f"137{_tag()}"
    store.otp_fail(phone, 10, 5)
    ttl1 = store._redis.ttl(store._fails_key(phone))
    time.sleep(2.2)
    store.otp_fail(phone, 10, 5)
    ttl2 = store._redis.ttl(store._fails_key(phone))
    assert ttl2 < ttl1, f"窗口应从首击倒计时（{ttl1}→{ttl2} 应递减）"
    store._redis.delete(store._fails_key(phone))


def test_get_backend_degrades_to_memory(monkeypatch):
    """Redis 不可用 → get_backend 降级 Memory 不抛（不 500 契约）"""
    from app.services.auth import otp_store

    otp_store.set_backend(None)
    # 清探针残留键（共享 dev Redis：上一用例若在 exists('__probe__') 后中断会留键致假阳）
    try:
        import redis as _r
        from app.core.config import settings as _st

        _r.Redis.from_url(_st.redis_url).delete("yishu:otp:cool:__probe__")
    except Exception:  # noqa: BLE001,S110 —— 清理尽力而为；Redis 真不可用由 get_backend 探活路径暴露，不在清理处闹
        pass

    def boom():
        raise ConnectionError("mock redis down")

    monkeypatch.setattr(otp_store, "RedisOtpStore", boom)
    backend = otp_store.get_backend()
    assert isinstance(backend, otp_store.MemoryOtpStore)
    assert otp_store.is_degraded() is True
    otp_store.set_backend(None)
