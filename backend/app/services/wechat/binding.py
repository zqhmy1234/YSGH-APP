"""微信 ↔ 忆述账号**绑定**（D09-1 · 功能修复波 域⑨ · P0）。

## 缺陷原貌

企微「微信客服」回调只给到 `FromUserName`（该客服账号下的 **openid**），而我们的用户
主键是 `users.unionid`（微信开放平台三端一致 ID）。两者之间**没有任何映射载体**：

- `user_wechat_bindings` 表自 schema.sql 起就存在，但**从未映射 ORM** ⇒ baseline 迁移
  `431bcaa8bd54` 把它当遗留空表 DROP 掉（本批补建，见迁移 `d9a0b1c2d3e4`）；
- 回调 `api/wechat.py` 于是**只调 `process_incoming(db, msg)`（无 user_id）** ⇒ 媒体不下载、
  不建 Content、不入管线、不产记忆：F6「微信收」主链实际**只落一行 wechat_messages**；
- `gateway.py` 明明已解析出 `from_user`，但**全仓无消费方**（audit 域⑨ D09-1 证据）。

## 本模块做什么

| 函数 | 作用 |
|---|---|
| `resolve_user_id(db, openid)` | 回调侧解析：openid → user_id（未绑定 → `None`，保持"只记录"语义） |
| `bind(db, user_id, openid, channel)` | 绑定/换绑（openid 唯一 → 冲突时**默认拒绝**，防劫持他人微信） |
| `list_bindings(db, user_id)` | 某用户已绑渠道（解绑/审计用） |
| `unbind(db, user_id, openid)` | 解绑（仅限本人） |

## 仍挂账（如实标注，勿当已完成）

真实线上要让「用户在微信客服里发一张照片 → 自动进他的忆述账号」，还需要
**客服侧 openid → unionid 的解析**（企微 `cgi-bin/kf/customer/batchget`，需客服 Secret
与真实凭证），以及"未绑定用户如何发起绑定"的产品流程（在 App 内确认绑定/解绑）。
本批提供的是**该链路缺的那一半载体**：绑定表 + 解析 + 绑定端点，使"绑定存在即主链贯通"
可端到端验证；凭证相关的自动绑定按台账挂账。
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import User, UserWechatBinding
from app.db.models.wechat import WECHAT_BINDING_CHANNELS

logger = logging.getLogger("yishu.wechat")

DEFAULT_CHANNEL = "wechat_kf"


class AlreadyBoundToOtherUser(RuntimeError):
    """该 openid 已绑定到**其它**账号（换绑需显式解绑或走人工核验，防劫持）"""


def normalize_openid(openid: str | None) -> str | None:
    """openid 归一（去空白；空串视为未提供）"""
    value = (openid or "").strip()
    return value or None


def resolve_user_id(db: Session, openid: str | None) -> str | None:
    """回调侧：openid → 绑定用户（未绑定 → None）。

    未绑定**不是**错误：企微回调必须始终 200/`success`（否则企微重推），
    此时按既有语义"只记录消息、不建内容"（`process_incoming` 的 user_id=None 分支）。
    """
    key = normalize_openid(openid)
    if not key:
        return None
    row = db.execute(
        select(UserWechatBinding).where(UserWechatBinding.openid == key)
    ).scalar_one_or_none()
    if row is not None:
        return str(row.user_id)

    # 2026-10-15（台账 §7.29）：直查未命中时**再试一次「客服 openid → unionid」解析** ——
    # 这是 F6 绑定的另一半：用户在 App 里微信授权过（`users.unionid` 已存在），而企微侧身份是
    # **客服 openid**，两者靠解析出的 unionid 对齐。解析成功则**顺手落一条 `wechat_kf` 绑定**
    #（下次直查即命中，省一次网络调用）。
    # ⚠️ 不改变既有语义：解析不到/无对应用户 ⇒ 仍返回 None（"只记录消息、不建内容"）。
    from app.services.wechat import kf_unionid

    unionid = kf_unionid.resolve_unionid(key)
    if unionid:
        user = db.execute(select(User).where(User.unionid == unionid)).scalar_one_or_none()
        if user is not None:
            try:
                bind(db, str(user.id), key, DEFAULT_CHANNEL)
                db.commit()
                logger.info("客服 openid 经 unionid 对齐成功并已落绑定 user=%s", user.id)
            except (AlreadyBoundToOtherUser, ValueError) as exc:
                logger.warning("客服 openid 对齐后落绑定失败（归属不受影响）: %s", exc)
            return str(user.id)
        logger.info("客服 openid 解析出 unionid 但库内无对应用户，按未绑定处理")

    logger.info("微信 openid 未绑定，仅记录消息: %s", key[:12])
    return None


def list_bindings(db: Session, user_id: str) -> list[UserWechatBinding]:
    """某用户全部绑定（按绑定时间倒序）"""
    return list(
        db.execute(
            select(UserWechatBinding)
            .where(UserWechatBinding.user_id == user_id)
            .order_by(UserWechatBinding.bound_at.desc())
        )
        .scalars()
        .all()
    )


def bind(
    db: Session,
    user_id: str,
    openid: str,
    channel: str = DEFAULT_CHANNEL,
    *,
    allow_rebind: bool = False,
) -> UserWechatBinding:
    """绑定当前用户与该 openid（同用户重复绑定幂等）。

    - 渠道白名单校验（`WECHAT_BINDING_CHANNELS`）；
    - openid 已被**他人**绑定且 `allow_rebind=False` → 抛 `AlreadyBoundToOtherUser`
      （调用方映射 409）。**不默认换绑**：静默改绑 = 把别人微信收到的记忆改投到我的账号；
    - 同用户重复绑定 → 幂等返回既有行（并刷新 channel/时间需显式允许时另行处理）。
    """
    key = normalize_openid(openid)
    if not key:
        raise ValueError("openid 不能为空")
    if channel not in WECHAT_BINDING_CHANNELS:
        raise ValueError(f"未知渠道 {channel!r}（合法值 {list(WECHAT_BINDING_CHANNELS)}）")

    user = db.get(User, user_id)
    if user is None:  # pragma: no cover —— 鉴权已保证用户存在；防御性保留
        raise ValueError("用户不存在")

    existing = db.execute(
        select(UserWechatBinding).where(UserWechatBinding.openid == key)
    ).scalar_one_or_none()
    if existing is not None:
        if str(existing.user_id) == str(user_id):
            return existing  # 幂等
        if not allow_rebind:
            raise AlreadyBoundToOtherUser(key)
        existing.user_id = user_id
        existing.channel = channel
        db.commit()
        db.refresh(existing)
        return existing

    row = UserWechatBinding(user_id=user_id, openid=key, channel=channel)
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_login_binding(db: Session, user_id: str, openid: str, channel: str = "") -> bool:
    """授权登录后**建立渠道绑定**（幂等）；成功 True，跳过/失败 False，**绝不抛给登录链**。

    2026-10-15（台账 §7.29）：这是「跳转微信 → 授权 → **完成**」里"完成"的那一半。
    原实现 `/api/v1/auth/wechat` 只建 `users` 行、**不写 `user_wechat_bindings`** ⇒
    企微侧 `resolve_user_id(openid)` 永远匹配不到人（F6 微信入口实际不可达）。

    口径：
      - `channel` 空 ⇒ 用 `DEFAULT_CHANNEL`；非法值由 `bind()` 抛 ValueError ⇒ 这里只记日志
        （**登录不能因为绑定渠道写错而失败**）；
      - 同一 openid 已绑**他人** ⇒ `AlreadyBoundToOtherUser` ⇒ **不换绑**（既定纪律：静默换绑
        会把别人微信收到的记忆改投到我的账号）⇒ 记日志、跳过；
      - 同用户重复登录 ⇒ `bind()` 幂等返回既有行 ✓。
    """
    key = normalize_openid(openid)
    if not key:
        return False
    try:
        bind(db, user_id, key, channel or DEFAULT_CHANNEL)
        db.commit()
    except AlreadyBoundToOtherUser:
        logger.warning("授权登录：openid 已绑定其它账号，跳过绑定（不换绑）openid 尾 4=%s", key[-4:])
        return False
    except ValueError as exc:
        logger.warning("授权登录：绑定参数非法，跳过绑定 user=%s: %s", user_id, exc)
        return False
    logger.info("授权登录已建立渠道绑定 user=%s channel=%s", user_id, channel or DEFAULT_CHANNEL)
    return True


def unbind(db: Session, user_id: str, openid: str) -> bool:
    """解绑（仅限本人；他人绑定视为不存在，不泄露信息）"""
    key = normalize_openid(openid)
    if not key:
        return False
    row = db.execute(
        select(UserWechatBinding).where(UserWechatBinding.openid == key)
    ).scalar_one_or_none()
    if row is None or str(row.user_id) != str(user_id):
        return False
    db.delete(row)
    db.commit()
    return True
