"""门禁机读产物**统一 schema + 单一写入实现**（D14-22 / B10-i）。

原状（审计域⑭记录）：三个门禁工具各写一套 JSON，**键集毫无交集**——
  · `review_agent` → `.cowork-temp/review-report.json`
        `{mode, passed, blocking_checks, env_blocked_checks, details, generated_at}`
  · `test_agent`   → `.cowork-temp/test-report.json`
        `{passed, blocking_sections, env_blocked_sections, details, cleanup_test_collections, generated_at}`
  · `audit_harness` → 任意 `--json PATH`
        `{critical: [...], info: [...]}`
且 `audit_harness` 的写入 `write_text` **无异常兜底**——路径不可写即整个审计崩掉。

现收敛：
  · 统一加 **`schema_version`** 信封，并保证 `generated_at` 存在 ⇒ 三工具报告有**公共字段**
    （`COMMON_KEYS`），消费方可按同一套键取用；
  · 写入走**带兜底的单一实现**：写失败只出 `[WARN]`，**不抛异常、不改变门禁判定**
    （报告是诊断留档产物；写不进去应"可见告警"，而非让工具崩或被误判为通过）。
  各工具**自有键一律保留**（CI 依赖 `details`/`blocking_checks`，不得删改）。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

SCHEMA_VERSION = 1

# 三工具机读报告的公共字段（消费方/测试可据此断言"已统一"）
COMMON_KEYS: tuple[str, ...] = ("schema_version", "generated_at", "passed")


def enrich(payload: dict) -> dict:
    """补统一信封（不改动各工具自有键；`generated_at` 已存在则不覆盖）。"""
    out = dict(payload)
    out.setdefault("generated_at", datetime.now().astimezone().isoformat(timespec="seconds"))
    out["schema_version"] = SCHEMA_VERSION
    return out


def write_report(path: str | Path, payload: dict) -> bool:
    """写机读报告（统一信封 + 异常兜底）。返回 True=已写 / False=写失败。"""
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(enrich(payload), ensure_ascii=False, indent=2), encoding="utf-8")
        return True
    except Exception as exc:  # noqa: BLE001 — 落盘失败原因需原样可见
        print(f"[WARN] 机读报告写入失败（{type(exc).__name__}: {exc}）：{p}", file=sys.stderr)
        return False
