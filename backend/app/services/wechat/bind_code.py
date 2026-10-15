"""微信绑定码（D09-1 配套 · 2026-10-15 · 台账 §4.20 ㉖）。

## 为什么需要它（产品级缺口，非接线问题）

F6「微信入口」要成立，必须先知道**用户的微信 openid** —— 而 openid 只存在企微平台侧，
App 无从获取（`binding.bind_wechat` 收的是调用方**已知**的 openid，后端也没有任何
"绑定码/扫码"流程）。故流程设计为：

    ① App（已登录）调 `POST /api/v1/wechat/bind-code` 领 6 位码（Redis，10 分钟）；
    ② 用户在自己的微信里给忆述发一句「绑定 123456」（或直接发 123456）；
    ③ 企微回调进来 → `resolve_user_id` 解析不到（尚未绑定）→ 走本模块**消费码**
       → 建立 openid ↔ 账号 绑定 ⇒ 此后消息成为记忆（F6 主链打通）。

## 口径（与既有纪律对齐）

- **单次使用**：消费用 Redis `GETDEL`（原子；老版本 Redis 退化 get+delete，见 `consume_code`）；
- **重发即失效**：同一用户新码会作废旧码（双键：`code → uid` 与 `uid → code`）；
- **不做换绑**：若该 openid 已绑定**他人**，`bind_wechat` 会抛 `AlreadyBoundToOtherUser`
  （调用方拒绝并留日志）——静默换绑会把别人微信收到的记忆改投到我账号（既定纪律）；
- **可注入**：Redis 客户端是模块级 `_client`，测试可替换为内存桩（同 `ports.py` 依赖反转口径）。
"""
from __future__ import annotations

import logging
import secrets

from app.core.config import settings

logger = logging.getLogger("yishu.wechat")

# 绑定码有效期（秒）。10 分钟：够用户切到微信发一句话，又短到不该被翻出来复用。
CODE_TTL_SECONDS = 600

# 码长度（纯数字；用户要在微信里手打，6 位是"好念好打"与"不可猜"的折中）
CODE_DIGITS = 6

_KEY_CODE = "wechat:bindcode:"          # code → user_id
_KEY_USER = "yishu:wechat:bindcode:user:"  # user_id → code（用于发新码时作废旧码）

_client = None


def _redis():
    """懒建 Redis 客户端（与 `app/workers/queue.py` 同源配置；解码为 str 便于断言）"""
    global _client  # noqa: PLW0603 —— 模块级单例（测试可整体替换）
    if _client is None:
        from redis import Redis

        _client = Redis.from_url(settings.redis_url, decode_responses=True)
    return _client


def set_client(client) -> None:  # noqa: ANN001 —— 测试注入用（duck-typed）
    """注入替身（内存桩）——生产不调用"""
    global _client  # noqa: PLW0603
    _client = client


def normalize_code(raw: str | None) -> str | None:
    """从用户消息里取出绑定码：去掉空白与常见前后缀（「绑定 123456」「123456」）。

    只认**纯数字且长度恰好为 CODE_DIGITS** 的片段：宽松匹配会把普通聊天里的数字
    （"今天花了 100000"）误当绑定码 ⇒ 宁可让用户按提示再发一次，也不误绑账号。
    """
    if not raw:
        return None
    digits = "".join(ch for ch in raw.strip() if ch.isdigit())
    if len(digits) != CODE_DIGITS:
        return None
    return digits


def issue_code(user_id: str) -> str:
    """为当前账号签发绑定码（并作废该账号旧码）。返回码本身。"""
    r = _redis()
    old = r.get(_KEY_USER + user_id)
    if old:
        r.delete(_KEY_CODE + str(old))
    code = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    r.set(_KEY_CODE + code, user_id, ex=CODE_TTL_SECONDS)
    r.set(_KEY_USER + user_id, code, ex=CODE_TTL_SECONDS)
    return code


def consume_code(raw: str | None) -> str | None:
    """消费绑定码 → 命中的 user_id；无码/错码/过期/已用过 → None。

    单次使用：优先 `GETDEL`（Redis ≥6.2 原子删除）；老版本退化 get+delete
    （极端并发下理论上可能两人同码——概率与影响都极低，且码本身有时效）。
    """
    code = normalize_code(raw)
    if code is None:
        return None
    r = _redis()
    key = _KEY_CODE + code
    try:
        user_id = r.getdel(key)
    except AttributeError:  # pragma: no cover —— 老版本 redis-py / Redis
        user_id = r.get(key)
        if user_id is not None:
            r.delete(key)
    if not user_id:
        return None
    user_id = str(user_id)
    r.delete(_KEY_USER + user_id)  # 账号侧索引一并清（避免残留旧码可再签发）
    return user_id
