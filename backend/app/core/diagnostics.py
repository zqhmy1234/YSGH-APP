"""启动期诊断（2026-10-14 · 台账 §7.12「静默降级显性化」）。

## 为什么单独一个模块

backend 与 agent 之间的共享密钥**必须同值**（同一把密钥、两个方向——
见 `app/api/deps.py:agent_service_user` 的说明）。配置不一致时的形态很坏：

  · backend 有值、agent 没配 ⇒ agent 不校验入站头（本地放行），但 agent → backend
    的 W1/W2 无 token ⇒ **记忆镜像与检索合并静默降级**（AI 说"已记住"、卡片也出，
    而 App 时间轴 / 检索一片空白）；
  · 两侧都配了但**不是同一个值** ⇒ backend → agent 401、agent → backend 401 ⇒ 同上。

两种情况下**两侧日志都只显示"已配置"**，没有任何人能看出差异。指纹
（sha256 前 8 位）让两侧的**启动日志可以直接对账**：同一指纹＝同一密钥，
不同指纹＝必须立刻修。指纹不可逆，且**只进日志**（不进 `/healthz` 等对外响应，
避免把弱密钥变成可爆破的预言机）。

## 与 agent 侧的对应

同名实现：`agent/src/yishu/security.py:token_fingerprint`（同一算法：
sha256 十六进制前 8 位，空值返回 `"(空)"`）。**两侧改动必须同步**。
"""
from __future__ import annotations

import hashlib
import logging

from app.core.config import settings

logger = logging.getLogger("yishu.diagnostics")


def token_fingerprint(raw: str | None) -> str:
    """令牌指纹（sha256 前 8 位十六进制）；空值返回 `"(空)"`。"""
    value = (raw or "").strip()
    if not value:
        return "(空)"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def log_agent_channel_config() -> None:
    """启动期打印服务间通道配置（供与 agent 启动日志对账）。**不打印密钥本身**。

    为什么要在**启动期**打：运行期不一致的表现是"AI 页照常能聊，但记忆不落 App"，
    排查要从业务栈翻到配置差异；启动日志里就有指纹，一眼即可判定。
    """
    token = settings.agent_service_token or ""
    if not token and settings.app_env == "production":
        logger.error(
            "服务间通道未配置（agent_service_token 为空）且 APP_ENV=production："
            "agent 的记忆镜像（W1）与检索合并（W2）将被 **静默跳过** —— "
            "用户会看到「AI 说已记住，App 时间轴却空白」。请配置后重启。",
        )
    logger.info(
        "服务间通道：agent_base=%s token=%s fp=%s ｜ agent 侧 AGENT_SERVICE_TOKEN "
        "必须是同一个值（比对两侧启动日志的 fp）",
        settings.agent_service_base_url,
        "已配置" if token else "未配置",
        token_fingerprint(token),
    )
