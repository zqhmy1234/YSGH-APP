#!/usr/bin/env python3
"""校验 `deploy/.env.production.template` 与 `backend/app/core/config.py` 是否仍然对齐。

为什么需要它：模板里写着「与 config.py 字段逐一对齐」，但这句话在 2026-09-21 之前**只是口头承诺**——
新增一个 config 字段而忘了加进模板，不会被任何门禁发现（`review_agent.py` 不看 env 模板）。
本脚本把该承诺变成可复现判据；`deploy/RUNBOOK.md` §1.2 与 `deploy/README.md` 均引用它。

用法（仓库根）：
    python deploy/scripts/check_env_template.py
退出码：0 = 对齐；1 = 存在真缺口或过期项（输出会逐条列出）。

判定规则：
  · **真缺口**（阻断）：config 有该字段，但模板里既没有生效行也没有注释行 → 部署时会漏配。
  · **过期项**（阻断）：模板里有该键，但 config 已无此字段 → 拼写错误或字段已删（会误导运维）。
  · **非 config 键**（豁免）：容器参数（compose 用）与「待接 key 备忘」两族**本就不该**出现在 config 里。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
TEMPLATE = ROOT / "deploy" / ".env.production.template"

# 这些键属于「非 config」——compose 容器参数 与 待接 key 备忘，config.py 里没有它们是**正确的**
NON_CONFIG_PREFIXES = ("POSTGRES_", "REDIS_", "QDRANT_", "PG_", "SMS_", "UNI_PUSH_")
NON_CONFIG_EXACT = {"API_PORT"}

_KEY_RE = re.compile(r"^([A-Z][A-Z0-9_]*)=(.*)$")


def main() -> int:
    if not TEMPLATE.exists():
        print(f"[FAIL] 模板不存在：{TEMPLATE}")
        return 1

    active: set[str] = set()
    commented: set[str] = set()
    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        is_comment = stripped.startswith("#")
        body = stripped.lstrip("#").strip() if is_comment else stripped
        m = _KEY_RE.match(body)
        if not m:
            continue
        (commented if is_comment else active).add(m.group(1).upper())

    sys.path.insert(0, str(ROOT / "backend"))
    from app.core.config import Settings  # noqa: E402  延迟导入：先做文件存在性检查

    fields = {k.upper() for k in Settings.model_fields}

    covered = active | commented
    missing = sorted(fields - covered)
    stale = sorted(
        k
        for k in covered - fields
        if not (k.startswith(NON_CONFIG_PREFIXES) or k in NON_CONFIG_EXACT)
    )

    print(f"[check] config 字段 {len(fields)} ｜ 模板生效项 {len(active)} ｜ 模板注释项 {len(commented)}")
    print(f"[check] 真缺口（config 有、模板无）: {missing if missing else '无'}")
    print(f"[check] 过期项（模板有、config 无）: {stale if stale else '无'}")
    print(
        "[check] 豁免的非 config 键（容器参数/待接 key 备忘）: "
        f"{sorted(k for k in covered - fields if k not in stale)}"
    )

    if missing or stale:
        print("\n[FAIL] 模板与 config.py 已漂移——补齐后重跑（这是 `deploy/README.md` §3 的契约）。")
        return 1
    print("\n[OK] 模板与 config.py 对齐。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
