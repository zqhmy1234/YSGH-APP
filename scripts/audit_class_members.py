"""客户端 UTS 类「成员用了却没声明」检查（2026-09-25 新增 · GAP-2 实测暴露的假绿门）。

背景（实测）：B5.2f-2e 把 `saving` 状态从 `RecordSheet.uvue` 迁入 `useVoiceRecord.uts`
时，**方法体里写了 `this.saving.value` 却没有声明 `saving` 字段**，而：
  · HBuilderX 编译**报绿**（GAP-1：编译只覆盖语法/解析，不覆盖符号解析）；
  · 轴 6（`audit_client_imports`）也**抓不到**——它只查「用了模块导出却没 import」，
    `this.<成员>` 是**类成员访问**、不是跨模块符号。
⇒ 「跨模块符号」与「类成员」是两道独立的假绿面；本轴补第二道。

规则：对 `client/**/*.uts` 里每个 `class`（**`extends` 的类跳过**——继承来的成员静态不可知，
报出来会是误报），收集
  · 声明集：深度 0 的字段（`name = ...` / `name: T`）与方法（`name(`）；
  · 使用集：方法体内的 `this.<name>`；
若使用集里有名字不在声明集 ⇒ **CRITICAL**（真机必 `undefined.value` 崩溃）。

用法：python scripts/audit_class_members.py [--json PATH]
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
SKIP_DIRS = ("uni_modules", "unpackage", "node_modules", ".git")

CLASS_RE = re.compile(r"(?:export\s+)?(?:abstract\s+)?class\s+([A-Za-z_]\w*)([^{]*)\{")
PREFIX = r"(?:(?:private|public|protected|static|readonly|override|async|declare)\s+)*"
FIELD_ASSIGN_RE = re.compile(r"^[ \t]*" + PREFIX + r"([A-Za-z_]\w*)\s*(?::[^=\n]*?)?\s*=\s*")
FIELD_TYPE_RE = re.compile(r"^[ \t]*" + PREFIX + r"([A-Za-z_]\w*)\s*:\s*[^=\n]+$")
METHOD_RE = re.compile(r"^[ \t]*" + PREFIX + r"([A-Za-z_]\w*)\s*\(")
THIS_RE = re.compile(r"this\.([A-Za-z_]\w*)")


def _iter_sources():
    for p in CLIENT.rglob("*.uts"):
        if not p.is_file():
            continue
        if any(s in p.parts for s in SKIP_DIRS):
            continue
        yield p


def _class_bodies(code: str):
    """产出 (类名, 是否 extends, 类体文本)。用花括号配平取类体（字符串已由 strip_comments 清掉）。"""
    for m in CLASS_RE.finditer(code):
        header_tail = m.group(2)
        start = m.end() - 1  # 指向 '{'
        depth = 0
        i = start
        while i < len(code):
            c = code[i]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        body = code[start + 1:i]
        has_extends = "extends" in header_tail
        yield m.group(1), has_extends, body


def _declared_and_used(body: str) -> tuple[set[str], set[str]]:
    declared: set[str] = set()
    depth = 0
    for line in body.splitlines():
        stripped = line.strip()
        if depth == 0 and stripped:
            for rx in (FIELD_ASSIGN_RE, FIELD_TYPE_RE, METHOD_RE):
                mm = rx.match(line)
                if mm:
                    declared.add(mm.group(1))
                    break
        depth += line.count("{") - line.count("}")
    used = set(THIS_RE.findall(body))
    return declared, used


def scan() -> tuple[list[dict], int, int]:
    """返回 (违规列表, 已检查类数, 因 extends 跳过的类数)。"""
    violations: list[dict] = []
    checked = 0
    skipped = 0
    for p in _iter_sources():
        text = p.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(text)
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        for cls, has_extends, body in _class_bodies(code):
            if has_extends:
                skipped += 1
                continue
            checked += 1
            declared, used = _declared_and_used(body)
            for name in sorted(used - declared):
                violations.append({"file": rel, "class": cls, "member": name})
    return violations, checked, skipped


def main() -> int:
    violations, checked, skipped = scan()
    payload = {"tool": "audit_class_members", "passed": not violations,
               "violations": violations, "checked_classes": checked, "skipped_extends": skipped}
    out = None
    for i, a in enumerate(sys.argv):
        if a == "--json" and i + 1 < len(sys.argv):
            out = Path(sys.argv[i + 1])
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if violations:
        print(f"[CRITICAL] {len(violations)} 处「类成员用了却没声明」（this.<名> 不在声明集）：")
        for v in violations:
            print(f"  - {v['file']}: class {v['class']} 的 this.{v['member']}")
        return 1
    print(f"[OK] 客户端 UTS 类成员解析一致（检查 {checked} 类；{skipped} 个 extends 类跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
