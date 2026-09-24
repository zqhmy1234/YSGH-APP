"""客户端硬编码色字面量棘轮（令牌波 P4 · 2026-09-25）。

背景：令牌波开工实测 —— `client/**` 里有 **hex 762 次 / 56 种**、**`rgb()/rgba()` 190 次 / 72 种**、
**渐变 10 种**，而 `design_tokens.dtcg.json` 的令牌在样式里被引用 **0 次**（`var(--` 出现 0 次）。
令牌化（P3）是**逐域、跨多批**的长活；若没有尺子，一边收敛一边长新字面量，永远收不干净。

口径（与轴 5/6 一致的棘轮制）：
  · 扫描面：`client/**/*.{uvue,css}`，排除 `uni_modules` / `unpackage` / `node_modules`；
  · 计数三类：`hex`（#RGB/#RRGGBB/#RRGGBBAA）、`rgb`（`rgb()/rgba()`）、`gradient`（`linear-gradient(...)`）；
  · 注释剔除：复用轴 6 的 `strip_comments`（`.uvue` 模板区只剔 HTML 注释，避免吞模板代码 ⇒ 只会多算不会少算）；
  · 棘轮：**三类总数任一上升** 或 **任一文件任一计数上升** ⇒ CRITICAL；下降 ⇒ WARN（要求同步下调基线）；
  · 僵尸豁免清算（同轴 5/6）：基线 `per_file` 中已归零的条目 ⇒ CRITICAL（要求删除该条）。

用法：
  python scripts/audit_token_literals.py                  # 只看现状
  python scripts/audit_token_literals.py --write-baseline # 把现状写入 audit_harness_baseline.json
退出码：0 通过 / 1 违规
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:
    from gate_io import force_utf8  # noqa: E402
except ModuleNotFoundError:
    from scripts.gate_io import force_utf8  # noqa: E402

try:
    from audit_client_imports import strip_comments  # noqa: E402
except ModuleNotFoundError:
    from scripts.audit_client_imports import strip_comments  # noqa: E402

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"
BASELINE = ROOT / "scripts" / "audit_harness_baseline.json"
SKIP_DIRS = ("uni_modules", "unpackage", "node_modules", ".git")

HEX_RE = re.compile(r"#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})\b")
RGB_RE = re.compile(r"rgba?\([^)]*\)")
GRAD_RE = re.compile(r"linear-gradient\([^)]*\)")


def _iter_sources():
    for ext in ("*.uvue", "*.css"):
        for p in CLIENT.rglob(ext):
            if p.is_file() and not any(s in p.parts for s in SKIP_DIRS):
                yield p


def _counts(text: str) -> dict[str, int]:
    """注释剔除后计数。渐变里的 rgba 不再重复计入 rgb（先剔渐变再数 rgb）。"""
    code = strip_comments(text)
    grad = GRAD_RE.findall(code)
    rest = GRAD_RE.sub("", code)
    return {"hex": len(HEX_RE.findall(rest)), "rgb": len(RGB_RE.findall(rest)), "gradient": len(grad)}


def scan() -> tuple[dict[str, dict[str, int]], dict[str, int]]:
    """返回 (per_file 计数, 三类总数)。"""
    per_file: dict[str, dict[str, int]] = {}
    total = {"hex": 0, "rgb": 0, "gradient": 0}
    for p in _iter_sources():
        c = _counts(p.read_text(encoding="utf-8", errors="replace"))
        if any(c.values()):
            per_file[p.relative_to(ROOT).as_posix()] = c
            for k in total:
                total[k] += c[k]
    return per_file, total


def findings(base: dict | None = None) -> tuple[list[str], list[str], list[str]]:
    """(CRITICAL, WARN, INFO) —— 供 CLI 与 audit_harness 轴共用。"""
    if base is None:
        base = json.loads(BASELINE.read_text(encoding="utf-8")).get("token_literals", {})
    per_file, total = scan()
    crit: list[str] = []
    warn: list[str] = []
    info: list[str] = []
    b_total = base.get("total")
    b_files: dict[str, dict[str, int]] = base.get("per_file", {})
    if b_total is None:
        info.append(f"token_literals: 基线缺失（当前 {total}）——请跑 --write-baseline")
        return crit, warn, info
    for k in ("hex", "rgb", "gradient"):
        if total[k] > int(b_total[k]):
            crit.append(f"[令牌] 硬编码 {k} 字面量上升：{b_total[k]} → {total[k]}（令牌化期间只许减不许增）")
        elif total[k] < int(b_total[k]):
            warn.append(f"[令牌] 硬编码 {k} 字面量已下降 {b_total[k]} → {total[k]}——请同步下调基线（棘轮只许收缩）")
    for f, c in sorted(per_file.items()):
        bc = b_files.get(f)
        if bc is None:
            crit.append(f"[令牌] 新增含色字面量的文件 {f}（{c}）——请在基线补录或改用令牌")
            continue
        for k in ("hex", "rgb", "gradient"):
            if c[k] > int(bc.get(k, 0)):
                crit.append(f"[令牌] {f} 的 {k} 字面量上升 {bc.get(k, 0)} → {c[k]}")
    for f, _bc in sorted(b_files.items()):
        cur = per_file.get(f, {"hex": 0, "rgb": 0, "gradient": 0})
        if not any(cur.values()):
            crit.append(f"[令牌] 基线 per_file 的 {f} 已归零（文件删除/改名/已令牌化）——请从基线删除该条")
    cur_txt = f"hex {total['hex']} · rgb {total['rgb']} · gradient {total['gradient']}"
    info.append(f"[令牌] 现状：{cur_txt}（基线 {b_total}）")
    return crit, warn, info


def main() -> int:
    if "--write-baseline" in sys.argv:
        per_file, total = scan()
        full = json.loads(BASELINE.read_text(encoding="utf-8"))
        full["token_literals"] = {
            "_doc": "令牌波 P4 棘轮：硬编码色字面量（hex/rgb/gradient）只许减不许增；下降须同步下调基线。",
            "total": total,
            "per_file": per_file,
            "reason": "令牌波开工实测存量（2026-09-25 · 代码口径，已剔注释）：hex 638 / rgb 171 / gradient 10",
            "reason_note": "同期 design_tokens.dtcg.json（321 叶子）在样式里被引用 0 次",
            "target": "P3 逐域收敛后逐条下调，直至 total 归零（全部走令牌）",
        }
        BASELINE.write_text(json.dumps(full, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"[write-baseline] total={total} · 文件数={len(per_file)}")
        return 0
    crit, warn, info = findings()
    for m in crit:
        print("[CRITICAL]", m)
    for m in warn:
        print("[WARN]", m)
    for m in info:
        print("[INFO]", m)
    return 1 if crit else 0


if __name__ == "__main__":
    raise SystemExit(main())
