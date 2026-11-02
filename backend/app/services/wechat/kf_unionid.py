"""企微「微信客服」侧 openid → unionid 解析（2026-10-15 · 台账 §7.29）。

## 为什么需要它（F6 绑定的另一半）

决策 #8 定的是 **unionid 对齐**：App 侧微信授权拿到 unionid（写进 `users.unionid`），
而企微客服回调只给**客服侧 openid**（`binding.py` 模块注释早自述过这个缺口）⇒ 必须把
客服 openid 解析成同一个 unionid，才能把消息归属到人。

调用链：`GET /cgi-bin/gettoken`（客服 Secret）→ `POST /cgi-bin/kf/customer/batchget`
（`external_userid_list`）→ `customer_list[].unionid`（**仅当该用户已绑定开放平台**才返回，
否则只能拿到 nickname/external_userid ⇒ 解析不到就按"未绑定"处理）。

## 口径（与既有纪律对齐）

- **未配 `WECHAT_KF_SECRET` ⇒ 安全降级**：返回 None（语义=「解析不到」）⇒ 调用方继续走既有的
  「只记录、不归属」分支，企微回调照常 200。**不造假、不 mock**（同 §7.19 口径）；
- **超时必须短**：企微要求 5s 内响应，否则重推 ⇒ 本模块 3s 超时，失败即 None（留 WARNING）；
- **端口可注入**：模块级 `_gateway` 可在测试中替换为替身（同 `ports.py` 依赖反转口径）；
- **不缓存负结果**：用户可能在解析失败后才去绑定 ⇒ 每次未命中都实探（调用量＝未绑定用户的消息数，
  量级很小；真成热点再加短 TTL 负缓存）。
"""
from __future__ import annotations

import logging
import time
from typing import Protocol, runtime_checkable

from app.core.config import settings

logger = logging.getLogger("yishu.wechat")

KF_TOKEN_URL = "https://qyapi.weixin.qq.com/cgi-bin/gettoken"  # noqa: S105 —— 接口地址，非口令
KF_BATCHGET_URL = "https://qyapi.weixin.qq.com/cgi-bin/kf/customer/batchget"
_TIMEOUT_SECONDS = 3

# access_token 进程内缓存：{"token": str, "expires_at": float(epoch)}
_token_cache: dict[str, object] = {}


@runtime_checkable
class KfUnionidPort(Protocol):
    """客服 openid → unionid 端口（真实实现走企微 HTTP；测试可替换替身）"""

    def resolve_unionid(self, openid: str) -> str | None:
        """解析成功返回 unionid，否则 None（**不得抛异常**：调用方在回调链路上）"""
        ...


def _kf_access_token() -> str | None:
    """取客服应用 access_token（进程内缓存，提前 120s 失效）"""
    import httpx

    now = time.time()
    cached = _token_cache.get("token")
    if cached and float(_token_cache.get("expires_at") or 0) > now:
        return str(cached)
    resp = httpx.get(
        KF_TOKEN_URL,
        params={"corpid": settings.wechat_corp_id, "corpsecret": settings.wechat_kf_secret},
        timeout=_TIMEOUT_SECONDS,
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("errcode"):
        logger.warning("企微客服 access_token 获取失败: %s %s", data.get("errcode"), data.get("errmsg"))
        return None
    token = data.get("access_token")
    if not token:
        return None
    _token_cache["token"] = str(token)
    _token_cache["expires_at"] = now + float(data.get("expires_in") or 7200) - 120
    return str(token)


class _HttpKfGateway:
    """默认实现：企微客服 customer/batchget"""

    def resolve_unionid(self, openid: str) -> str | None:
        import httpx

        token = _kf_access_token()
        if not token:
            return None
        resp = httpx.post(
            KF_BATCHGET_URL,
            params={"access_token": token},
            json={"external_userid_list": [openid]},
            timeout=_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("errcode"):
            logger.warning("客服 batchget 失败: %s %s", data.get("errcode"), data.get("errmsg"))
            return None
        for item in data.get("customer_list") or []:
            unionid = (item or {}).get("unionid")
            if unionid:
                return str(unionid)
        return None


_gateway: KfUnionidPort = _HttpKfGateway()


def set_gateway(gateway: KfUnionidPort) -> None:
    """注入替身（测试用；生产不调用）"""
    global _gateway  # noqa: PLW0603 —— 模块级单例（依赖反转）
    _gateway = gateway


def resolve_unionid(openid: str) -> str | None:
    """客服 openid → unionid；未配置凭证/解析不到/上游异常 ⇒ 一律 None（调用方按未绑定处理）"""
    if not (settings.wechat_kf_secret and settings.wechat_corp_id):
        logger.info("未配 WECHAT_KF_SECRET/WECHAT_CORP_ID ⇒ 跳过客服 openid→unionid 解析")
        return None
    try:
        unionid = _gateway.resolve_unionid(openid)
    except Exception as exc:  # noqa: BLE001 —— 回调链路绝不能因解析失败而炸（企微会重推）
        logger.warning("客服 openid→unionid 解析异常（按未绑定处理）: %s", exc)
        return None
    if unionid:
        logger.info("客服 openid 解析出 unionid（尾 4=%s）", str(unionid)[-4:])
    return unionid
