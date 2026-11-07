"""客户端 UTS 类「成员用了却没声明」检查（2026-09-25 新增 · GAP-2 实测暴露的假绿门）。

背景（实测）：B5.2f-2e 把 `saving` 状态从 `RecordSheet.uvue` 迁入 `useVoiceRecord.uts`
时，**方法体里写了 `this.saving.value` 却没有声明 `saving` 字段**，而：
  · HBuilderX 编译**报绿**（GAP-1：编译只覆盖语法/解析，不覆盖符号解析）；
  · 轴 6（`audit_client_imports`）也**抓不到**——它只查「用了模块导出却没 import」，
    `this.<成员>` 是**类成员访问**、不是跨模块符号。
⇒ 「跨模块符号」与「类成员」是两道独立的假绿面；本轴补第二道，且同时覆盖两个面：
  ① **类内自指**：`this.<名>` 不在本类声明集（含字段/方法）；
  ② **实例指涉**：`const x = new Cls(...)` 之后 `x.<名>` 不在 `Cls` 声明集
     （L-02 抽 composable 后组件大量经实例调用，属同一假绿面）。

规则：
  · 声明集 = 类体**深度 0** 的字段（`name = …` / `name: T`）与方法（`name(`）；
  · **`extends` 的类整体跳过**——继承来的成员静态不可知，报出来必是误报；
  · 实例映射仅认「同文件内 `const|let|var x = new Cls(`」这一种写法；类名重复出现时取**并集**
    （宁可漏报不可误报）；
  · 类名在仓库内找不到（外部/平台类，如 `Map`/`Date`/`Error`）→ 跳过。

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
NEW_ASSIGN_RE = re.compile(
    r"(?:const|let|var)\s+([A-Za-z_]\w*)\s*(?::[^=\n]*?)?=\s*new\s+([A-Za-z_]\w*)\s*\("
)


def _iter_sources():
    for ext in ("*.uts", "*.uvue"):
        for p in CLIENT.rglob(ext):
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


def _declared(body: str) -> set[str]:
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
    return declared


def _collect_classes() -> tuple[dict[str, set[str]], int, int]:
    """仓库级 class → 声明集（类名重复取并集；extends 类跳过）。返回 (映射, 检查类数, 跳过数)。"""
    members: dict[str, set[str]] = {}
    checked = 0
    skipped = 0
    for p in _iter_sources():
        code = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        for cls, has_extends, body in _class_bodies(code):
            if has_extends:
                skipped += 1
                continue
            checked += 1
            members.setdefault(cls, set()).update(_declared(body))
    return members, checked, skipped


def scan() -> tuple[list[dict], int, int]:
    """返回 (违规列表, 已检查类数, 因 extends 跳过的类数)。"""
    members, checked, skipped = _collect_classes()
    violations: list[dict] = []
    seen: set[tuple[str, str, str, str]] = set()

    def _add(path: str, cls: str, member: str, kind: str) -> None:
        key = (path, cls, member, kind)
        if key in seen:
            return
        seen.add(key)
        violations.append({"file": path, "class": cls, "member": member, "kind": kind})

    for p in _iter_sources():
        code = strip_comments(p.read_text(encoding="utf-8", errors="replace"))
        rel = str(p.relative_to(ROOT)).replace("\\", "/")
        # ① 类内自指
        for cls, has_extends, body in _class_bodies(code):
            if has_extends:
                continue
            for name in sorted(set(THIS_RE.findall(body)) - _declared(body)):
                _add(rel, cls, name, "this")
        # ② 实例指涉（同文件 `const x = new Cls(` 映射）
        for var, cls in NEW_ASSIGN_RE.findall(code):
            known = members.get(cls)
            if known is None:
                continue
            # 同名变量**只能**由 `new Cls(` 赋值，才把它当该类实例——否则属遮蔽/复用
            # （实测：`uploader_batch.uts` 里 `held` 既是 `new UploadProgress(...)` 又是
            #  `heldPhotos()` 数组，仅按名映射会把 `held.length` 误报成 UploadProgress 成员）。
            assigns = list(re.finditer(rf"\b{re.escape(var)}\s*=(?!=|>)", code))
            if not assigns:
                continue
            ok = True
            for m in assigns:
                tail = code[m.end() : m.end() + 80].lstrip()
                if not re.match(rf"new\s+{re.escape(cls)}\s*\(", tail):
                    ok = False
                    break
            if not ok:
                continue
            for name in sorted(set(re.findall(rf"\b{re.escape(var)}\.([A-Za-z_]\w*)", code)) - known):
                _add(rel, cls, name, f"{var}.")
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
        print(f"[CRITICAL] {len(violations)} 处「类成员用了却没声明」（编译不覆盖符号解析，真机 undefined 崩溃）：")
        for v in violations:
            print(f"  - {v['file']}: class {v['class']} 的 {v['kind']}{v['member']} 未声明")
        return 1
    print(f"[OK] 客户端 UTS 类成员解析一致（检查 {checked} 类；{skipped} 个 extends 类跳过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
