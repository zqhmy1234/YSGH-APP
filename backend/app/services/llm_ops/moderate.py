"""moderate.py —— 内容安全护栏**唯一策略入口**（托管优先、chat 兜底、契约归一）

任务归属：重构批次 A1（重构侦察 P0-1 解环，2026-08-27）；
2026-09-25 功能修复波（D10-1/3/12）扩为**唯一策略入口 + 唯一契约来源**。

## 背景

- 重构 P0-1：原策略逻辑内联在 `llm_ops/base.py` 与 `llm_ops/guard_managed.py`，
  两者互相兜底形成全图唯一模块环 → 收敛到本模块，依赖方向为单向 DAG：
  `base.moderate → 本选择器 → guard_managed（托管检测）+ dashscope（chat 兜底）`。
- **D10-3（2026-09-25）**：本选择器此前只有回响一条链在用；4 条入库/审核主链
  （内容入库 / 照片 caption / ASR 转写 / 微信文本）各自直连 `external.dashscope`，
  绕过策略 ⇒ "托管优先"实际只在一条路径生效。现 4 条链一律经本入口。

## 判定契约（**唯一来源**，D10-1）

两级过滤 + 统一出口：

1. **规则层（免费、确定性、所有模式生效）**：`check_sensitive`
   → `reject`（直接拒发）/ `mask`（打码后继续送检，原内容不外发）/ `pass`。
2. **托管优先**：`qwen_response_check`（`X-DashScope-DataInspection`）；
   不可用/异常 → **chat 兜底** `dashscope.moderate`（规则预检 + qwen-flash 双保险，
   生产未配 key 时 fail-closed 拒发）。

返回字典**恒定含 `action`** ∈ {`allow`, `mask`, `reject`}：

| action | 含义 | pass |
|---|---|---|
| `reject` | 拒发（含"护栏不可用且生产环境"的 fail-closed） | False |
| `mask` | 放行但以 `masked_text` 入库/展示 | True |
| `allow` | 放行 | True |

调用方**必须**用 `verdict_action(verdict)` 判定，不要只看 `action` 键
（历史缺陷 D10-1：fail-closed 分支漏 `action` ⇒ 调用方两条 `if` 均不成立 ⇒
"明确判拒发却原文入库"）。

mock 模式：托管不可用 → 走 dashscope.moderate 的 mock 契约（规则命中即拦截，
否则放行），保持本地联调确定性。
"""
from __future__ import annotations

import logging
from typing import Any

from app.services.external import dashscope
from app.services.llm_ops.guard_managed import qwen_response_check

logger = logging.getLogger("yishu.llm_ops")

# 判定动作枚举（单一来源；新增动作须同步 `verdict_action` 与全部消费方）
VERDICT_ACTIONS = ("allow", "mask", "reject")

_REJECT = "reject"
_MASK = "mask"
_ALLOW = "allow"


def verdict_action(verdict: dict[str, Any]) -> str:
    """护栏判定的**唯一解读函数**（D10-1）：先看 `pass`，再看 `action`。

    - `pass is False` ⇒ `reject`（**无论是否带 `action` 键**）——这是 fail-safe 的关键：
      任何"判不放行"的返回都必须被当成拒发，而不是因为缺键被静默放行。
    - `pass is True` 且 `action == "mask"` ⇒ `mask`；其余 ⇒ `allow`。
    """
    if not verdict.get("pass", False):
        return _REJECT
    if verdict.get("action") == _MASK:
        return _MASK
    return _ALLOW


def is_rejected(verdict: dict[str, Any]) -> bool:
    """便捷判定：是否应拒发（调用方最常用的一问）"""
    return verdict_action(verdict) == _REJECT


def _with_action(verdict: dict[str, Any], action: str) -> dict[str, Any]:
    """给判定补 `action` 键（不改写已有键；`pass` 与 action 保持一致）"""
    out = dict(verdict)
    out.setdefault("action", action)
    out.setdefault("pass", action != _REJECT)
    return out


def moderate(text: str) -> dict[str, Any]:
    """护栏检测（**唯一策略入口**）：规则预检 → 托管优先 → chat 兜底

    返回 {"pass", "action", "reason", ...}（`action` 恒存在，见模块 docstring）。
    托管路径的 `detector="managed"` 与 chat 兜底的富字段（`matched`/`categories`/
    `masked_text`）都保留——`action` 归一不丢信息。
    """
    # 第一级：规则预检（免费、确定性、所有模式生效；与 dashscope.moderate 同源，
    # 目的是让**托管路径也享受规则层**：D10-3 前托管可用时会整段跳过规则层）
    from app.services.external.sensitive_words import check_sensitive

    rule = check_sensitive(text)
    if rule["action"] == _REJECT:
        return _with_action(rule, _REJECT)

    # 送托管的文本用**打码后**文本（原内容不外发；规则层 mask 语义保留在下面合并）
    candidate = rule.get("masked_text") or text

    try:
        verdict = qwen_response_check(candidate)
    except RuntimeError as exc:
        logger.info("托管护栏不可用，chat 兜底: %s", exc)
        fallback = dashscope.moderate(text)
        return _with_action(fallback, verdict_action(fallback))

    if not verdict.get("pass", False):
        merged = dict(verdict)
        merged["matched"] = merged.get("matched") or rule.get("matched") or []
        merged["categories"] = merged.get("categories") or rule.get("categories") or []
        return _with_action(merged, _REJECT)

    if rule["action"] == _MASK:
        # 托管放行**不撤销**规则层的打码结论（否则 PII/广告词会被原样入库）
        merged = dict(verdict)
        merged["masked_text"] = rule.get("masked_text")
        merged["matched"] = merged.get("matched") or rule.get("matched") or []
        merged["categories"] = merged.get("categories") or rule.get("categories") or []
        return _with_action(merged, _MASK)

    return _with_action(verdict, _ALLOW)
