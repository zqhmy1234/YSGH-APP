"""共享安全工具：鉴权、标识校验、限流（2026-10-04 新增）。

**为什么单独一个模块**：`main.py`（对话端点）与 `web/api_routes.py`（记忆浏览端点）
都需要同一套「校验 token + 校验 user_id + 限流」。此前两边各写一套、且
`api_routes.py` 那套**完全没有 token 校验**，导致：

    curl "http://<host>:8300/api/memories?user_id=<任意 uuid>"

即可读到**任何人**的全部记忆（内容、情绪、标签、位置）。这是本项目目前最严重的
越权点——比性能问题危险得多，因为它不会报错、只会安静地泄漏数据。

统一到本模块后，规则只有一份，加固不会漏掉旁路。

## 三条 fail-closed 原则（本模块的硬约束）

1. **缺 user_id ⇒ 拒绝**，绝不回落环境变量或字面量 `"default"`
   （`memory_tools` / `url_fetch_tools` 的同款漏洞已单独修，见那里的注释）。
2. **未配置共享密钥 ⇒ 本地放行 + 启动告警；`APP_ENV=production` ⇒ 启动即拒**
   （`fail-fast` 好过运行期放行）。
3. **校验失败只说「不通过」，不回显用户输入**，避免把探测载荷带进日志。
"""
from __future__ import annotations

import os
import re
import time
from collections import deque
from dataclasses import dataclass, field

from fastapi import HTTPException

# user_id 允许的字符集：UUID（后端主用）、短横线/下划线（测试与自定义账号）。
# **刻意排除** `.` `/` `\` 空格与控制字符——防路径穿越、防日志注入、防注入 SQL 片段。
_USER_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_THREAD_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]{1,128}$")


def is_production() -> bool:
    return (os.getenv("APP_ENV", "") or "").strip().lower() in {"production", "prod"}


def service_token() -> str:
    return (os.getenv("AGENT_SERVICE_TOKEN", "") or "").strip()


def require_token(provided: str | None) -> None:
    """共享密钥校验。

    未配置 `AGENT_SERVICE_TOKEN` 时**放行但记审计**（本地开发便利），
    而生产环境**启动时**就会拒绝（见 `assert_production_token_config`）——
    放到运行期才拒绝意味着「配置错了但跑起来了」，那正是要避免的形态。
    """
    expected = service_token()
    if not expected:
        # fail-open 唯一一个允许的场景：本地开发且未配 token。
        # 记审计是因为它在生产是严重配置问题。
        try:
            from yishu.observability import log_audit

            log_audit("token_not_configured", token_provided=bool(provided))
        except Exception:  # noqa: BLE001,S110 —— 审计失败不能连带阻断请求
            pass
        return
    if (provided or "").strip() != expected:
        try:
            from yishu.observability import log_audit

            log_audit("token_rejected", token_len=len(provided or ""))
        except Exception:  # noqa: BLE001,S110
            pass
        raise HTTPException(status_code=401, detail="X-Agent-Token 缺失或不匹配")


def assert_production_token_config() -> None:
    """生产环境必须在**启动时**就拒绝启动（fail-fast）。

    放到运行期才拒绝 = 服务能起来、能通过 `/health`、只在真实请求时 401 ——
    这类「看起来正常实际是假的」在本项目已吃过多次亏。
    """
    if is_production() and not service_token():
        raise RuntimeError(
            "生产环境（APP_ENV=production）必须配置 AGENT_SERVICE_TOKEN —— "
            "否则任何人都能直连本服务读写他人记忆。请配置后重启。"
        )


def validate_user_id(raw: str | None) -> str:
    """校验并返回 user_id；不合法直接 400。**没有任何兜底分支**。"""
    value = (raw or "").strip()
    if not value:
        raise HTTPException(status_code=400, detail="缺少 user_id")
    if not _USER_ID_RE.match(value):
        # 不回显原值：避免把探测载荷写进日志/响应
        raise HTTPException(status_code=400, detail="user_id 格式非法")
    return value


def validate_thread_id(raw: str | None, user_id: str) -> str:
    """校验 thread_id 并**校验归属**。

    归属规则：thread_id 必须**由 user_id 派生**（等于它，或以 `user_id:` 开头）。

    为什么必须有这条：checkpointer 按 `thread_id` 存 checkpoint
    （`memory.checkpoints`），所以只要 A 用户用过 thread `X`，B 用户指定同一个
    `X` 就能**读到 A 的对话历史**（agent 侧实测：第 2 轮会准确复述第 1 轮内容
    —— 这正是「记忆能力正常」的表现，也正因此这里才有泄漏面）。

    历史数据兼容：本项目后端一直是把 `thread_id` 直接传成 `user_id`（实测 UUID 相同），
    所以默认规则与现状一致，不破坏已有会话。若确有不符合派生规则的历史会话，
    设 `AGENT_THREAD_ID_STRICT=0` 临时放宽——**每次放宽都会记审计**。
    """
    value = (raw or "").strip() or user_id
    if not _THREAD_ID_RE.match(value):
        raise HTTPException(status_code=400, detail="thread_id 格式非法")

    strict = (os.getenv("AGENT_THREAD_ID_STRICT", "1") or "1").strip() not in {
        "0",
        "false",
        "False",
    }
    if not strict:
        try:
            from yishu.observability import log_audit

            log_audit("thread_id_strict_disabled", thread_id_len=len(value))
        except Exception:  # noqa: BLE001,S110
            pass
        return value

    if value != user_id and not value.startswith(f"{user_id}:"):
        # 跨用户复用 thread_id ⇒ 可能读到他人对话历史
        try:
            from yishu.observability import log_audit

            log_audit(
                "thread_id_ownership_violation",
                user_id=user_id,
                thread_id_len=len(value),
            )
        except Exception:  # noqa: BLE001,S110
            pass
        raise HTTPException(status_code=403, detail="thread_id 不属于当前用户")
    return value


@dataclass
class _Bucket:
    """单用户滑动窗口计数（比令牌桶简单，够用且无后台线程）。"""

    hits: deque = field(default_factory=deque)


class RateLimiter:
    """按 user_id 的限流（内存滑动窗口）。

    为什么需要：单用户可以狂刷 `/v1/chat`，直接打爆 LLM 配额与账单。
    本机/内测规模用内存窗口足够；多副本部署时应换成 Redis 实现（接口不变）。

    局限（诚实标注）：**进程内**状态——多副本时每个副本各算各的。
    达到上限返回 429 而不是静默丢弃，让调用方能如实告诉用户「稍后再试」。
    """

    def __init__(self, max_calls: int, window_s: float) -> None:
        self.max_calls = max(1, int(max_calls))
        self.window_s = max(1.0, float(window_s))
        self._buckets: dict[str, _Bucket] = {}

    @classmethod
    def from_env(cls) -> RateLimiter:
        rpm = os.getenv("AGENT_RATE_LIMIT_RPM", "")
        try:
            rpm_i = int(rpm) if rpm else 20
        except (TypeError, ValueError):
            rpm_i = 20
        return cls(max_calls=rpm_i, window_s=60.0)

    def check(self, user_id: str) -> tuple[bool, int]:
        """返回 `(是否放行, 剩余额度)`；超限记审计并让调用方返回 429。"""
        # 顺带回收长期不活跃的桶：原先 `forget_idle` 只有定义、全仓无调用方，
        # ⇒ user_id 维度内存单调增长（每个见过的新用户一个 deque，永不释放）。
        # 挂在每次 check 上是最省事的触发点（无需后台线程，且请求驱动的回收时机足够）。
        self.forget_idle()
        now = time.monotonic()
        cutoff = now - self.window_s
        bucket = self._buckets.get(user_id)
        if bucket is None:
            bucket = _Bucket()
            self._buckets[user_id] = bucket
        while bucket.hits and bucket.hits[0] < cutoff:
            bucket.hits.popleft()
        if len(bucket.hits) >= self.max_calls:
            try:
                from yishu.observability import log_audit

                log_audit(
                    "rate_limited",
                    user_id=user_id,
                    limit=self.max_calls,
                    window_s=self.window_s,
                )
            except Exception:  # noqa: BLE001,S110
                pass
            return False, 0
        bucket.hits.append(now)
        return True, self.max_calls - len(bucket.hits)

    def forget_idle(self, max_idle_buckets: int = 4096) -> None:
        """回收长期不活跃的桶（避免 user_id 变多时内存无界增长）。"""
        if len(self._buckets) <= max_idle_buckets:
            return
        now = time.monotonic()
        cutoff = now - self.window_s
        stale = [k for k, b in self._buckets.items() if not b.hits or b.hits[-1] < cutoff]
        for key in stale[: len(self._buckets) - max_idle_buckets]:
            self._buckets.pop(key, None)
