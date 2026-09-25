"""生成态输出护栏（D10-8 · 2026-09-25 功能修复波 · 用户拍板「按建议来」）。

## 为什么需要

护栏此前只作用于**用户原文**（`contents` / `asr` / `wechat` / `echo`），而**用户可见的
生成文本**四条链**全无防线**：

| 链 | 产物 | 用户在哪看到 |
|---|---|---|
| `ai_tagging.generate_photo_description` | 照片 AI 描述 | 详情页 |
| `llm_ops/event_merge._llm_verdict` | 事件名/描述 | 时间轴卡片 |
| `llm_ops/annotate._normalize_hits` | 画像**开放新值**（≤8 字） | 画像页 |
| `api/chat` | Agent 回复 | 对话页 |

后果：用户拍一张"纸上写着违规内容"的照片，模型把那段话**复述**成照片描述；对话回复同理。
UGC/AI 生成内容审核是上架硬要求，而"生成态"是唯一完全无防线的面。

## 采用口径（拍板：选项 B + C）

| 档 | 做法 | 覆盖 |
|---|---|---|
| **B（全链）** | 四条链**同步走规则层**（本地词表+正则，零成本、确定性） | 全部生成文本 |
| **C（对话）** | `api/chat` 追加一次**托管护栏**（LLM 级）——直接对用户可见且量小 | 对话回复 |
| **C（照片）** | 照片描述量大 ⇒ 规则层全量 + **确定性抽样**送 LLM 复核；抽样命中即**不落库** | 照片描述 |

**成本**：规则层零成本；LLM 级仅两处——对话（每次一问一答 1 次）与照片抽样
（`generated_guard_sample_rate`，默认 0.1 ⇒ 100 用户 × 30 张/天 ≈ 300 次/天，
而非"全量 3000 次/天"）。

## fail-safe 口径

`llm_screen` 走 `llm_ops.moderate`（托管优先 + chat 兜底 + 规则层预检），并用
`moderate.verdict_action` 统一解读——**缺 `action` 键也按 `reject` 处理**（D10-1 的教训：
调用方按 `action` 分派时，任何"判不放行"的返回都必须被当成拒发）。
"""
from __future__ import annotations

import hashlib
import logging
from typing import Any

logger = logging.getLogger("yishu.output_guard")

# 抽样判定的哈希空间（2**64）
_HASH_SPACE = float(1 << 64)


def rule_screen(text: str) -> dict[str, Any]:
    """规则层筛查（同步、零成本、确定性；所有模式一致生效）。

    只看**硬规则**（reject 类：涉政/色情/涉枪涉爆/违规网址域名）——`mask` 类
    （广告词/号码）在生成态不做打码，交由调用方按需处理（生成文本本就不该出现号码）。
    """
    from app.services.external.sensitive_words import check_sensitive

    verdict = check_sensitive(text or "")
    action = verdict.get("action", "pass")
    return {
        "pass": action != "reject",
        "action": action,
        "matched": verdict.get("matched", []),
        "categories": verdict.get("categories", []),
        "detector": "rule",
        "reason": "rule-reject" if action == "reject" else "",
    }


def llm_screen(text: str) -> dict[str, Any]:
    """LLM 级护栏（托管优先 + chat 兜底 + 规则层预检；fail-safe 拒发）。

    判定用 `moderate.verdict_action` 统一解读（D10-1）：`pass is False` ⇒ `reject`，
    缺 `action` 键同样判 `reject`——不允许"判不放行却因为没有 action 键被放行"。
    """
    from app.services.llm_ops.base import moderate
    from app.services.llm_ops.moderate import verdict_action

    verdict = moderate(text or "")
    action = verdict_action(verdict)
    return {
        "pass": action != "reject",
        "action": action,
        "matched": verdict.get("matched", []),
        "categories": verdict.get("categories", []),
        "detector": verdict.get("detector", "managed"),
        "reason": verdict.get("reason", ""),
    }


def screen_generated(text: str, *, deep: bool = False) -> dict[str, Any]:
    """生成态文本统一筛查入口。

    - `deep=False`（默认）：只跑规则层（成本≈0）；
    - `deep=True`：规则层通过后再跑一次 LLM 级（供对话回复与照片抽样使用）；
    - 空文本直接放行（无内容可审，避免"空串→调 LLM"的浪费）。
    """
    if not (text or "").strip():
        return {"pass": True, "action": "allow", "matched": [], "categories": [], "detector": "empty", "reason": ""}
    verdict = rule_screen(text)
    if not verdict["pass"] or not deep:
        return verdict
    return llm_screen(text)


def sampled(token: str, rate: float) -> bool:
    """确定性抽样：`sha256(token)` 前 8 字节 / 2**64 < rate。

    - **确定性**：同一 token 每次判定一致（同一张照片不会"这次审、下次不审"）；
    - `rate <= 0` ⇒ 全部不抽；`rate >= 1` ⇒ 全抽；
    - 抽样只用于**成本控制**（LLM 级复核），规则层始终全量。
    """
    if rate <= 0:
        return False
    if rate >= 1:
        return True
    digest = hashlib.sha256((token or "").encode("utf-8")).digest()[:8]
    return (int.from_bytes(digest, "big") / _HASH_SPACE) < rate
