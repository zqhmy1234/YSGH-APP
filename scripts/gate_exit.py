"""门禁工具**退出码口径**的单一来源（D14-18 · 2026-09-24 重构波 B10-o）。

统一约定（此前 `review_agent` / `test_agent` 只有 0/1，而 `audit_harness` /
`check_schema_drift` / `gen_openapi` 用 2 表示"环境错误" ⇒ 同一个"跑不了"在不同工具里
语义不一致，且"环境没配好"会被误读成"代码有问题"）：

    0 = 通过（可提交）
    1 = 存在**违规**阻断项（代码问题，须修复后再提交）
    2 = **环境错误**（工具/依赖/后端不可用，门禁**无法真正判定**）

调用方（git hook / CI）对**非 0 一律阻断**；退出码只用于快速归因。

判定方式：失败项的消息以 `ENV_ERR_PREFIX` 开头即视为"环境错误"——
比给每个检查加返回值维度更轻，且报告里**肉眼可见**。
"""
from __future__ import annotations

ENV_ERR_PREFIX = "[环境错误]"


def classify_failure(blocking: dict[str, tuple[bool, str]]) -> tuple[list[str], list[str]]:
    """把失败检查分流为 `(环境错误, 违规)`。

    · 消息以 `ENV_ERR_PREFIX` 开头 → 环境错误（退出码 2）；
    · 其余 → 违规（退出码 1）；
    · 两者同时存在时**违规优先**（更具体、更可行动）。
    纯函数，便于各自工具用探针直接验证。
    """
    env_blocked = sorted(
        k for k, (ok, out) in blocking.items() if not ok and str(out).startswith(ENV_ERR_PREFIX)
    )
    real_fail = sorted(
        k for k, (ok, out) in blocking.items() if not ok and not str(out).startswith(ENV_ERR_PREFIX)
    )
    return env_blocked, real_fail
