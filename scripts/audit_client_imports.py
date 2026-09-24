"""客户端客户端模块符号「用了却没 import」检查（2026-09-24 新增 · B5.2f 实测暴露的假绿门）。

背景（实测）：HBuilderX CLI 的「项目 client 编译成功」**只覆盖语法/解析**，**不覆盖符号解析**——
把 `const ra = new RecordAnimations()` 写进 `.uvue` 却**完全没有 import**（B5.2f-2a 实况），
编译仍报「成功」（对比：import 丢逗号这类**语法**错误会被 `[vue/compiler-sfc] Unexpected token` 抓到）。
⇒ 「编译通过」不足以证明跨模块引用成立，需要静态补一道。

规则：对 `client/composables/**` 与 `client/utils/**` 里 `export` 的符号，只要在某 `.uvue`/`.uts`
文本中被**使用**（含 `new X(` / `X(` / `X.value` 等），该文件就必须在**某条 import 语句**里出现过
该符号名；若该文件自己声明了同名符号（`function/const/let/class/var X`），则视为本地符号、跳过。

用法：python scripts/audit_client_imports.py [--json PATH]
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

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"
MODULE_DIRS = (CLIENT / "composables", CLIENT / "utils")
SKIP_DIRS = ("uni_modules", "unpackage", "node_modules", ".git")

EXPORT_RE = re.compile(r"^[ \t]*export\s+(?:async\s+)?(?:function|const|let|class|var)\s+([A-Za-z_]\w*)", re.M)
EXPORT_LIST_RE = re.compile(r"^[ \t]*export\s*\{([^}]*)\}", re.M)
DECL_RE_TPL = r"^[ \t]*(?:function|const|let|class|var)\s+{name}\b"
IMPORT_BLOCK_RE = re.compile(r"^[ \t]*import\s+[\s\S]*?from\s+'[^']+'", re.M)
USE_TPL = r"(?<![\w.$]){name}\b"

# 注释剔除（B5.2f 实测：`enqueueDeleteOp` 等 26 例中多数只是**注释里提到**该符号 ⇒ 误报）。
# 只做保守剔除：HTML 注释整段、`/* */` 整段、`//` 至行尾（字符串里出现 `//` 的极端情况会吞掉
# 该行剩余内容 ⇒ 只可能**漏报**不会误报，对本门禁是可接受方向）。
HTML_COMMENT_RE = re.compile(r"<!--[\s\S]*?-->")
BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
LINE_COMMENT_RE = re.compile(r"//[^\n]*")
# 字符串字面量剔除（同类误报：`console.log('[yishu] createTextContent -> ' + id)` 里提到的符号名
# 被当成"使用"）；模板串不跨行，保守非贪婪匹配即可。
STRING_RE = re.compile(r"'[^'\n]*'|\"[^\"\n]*\"|`[^`\n]*`")


def _strip_js(text: str) -> str:
    text = BLOCK_COMMENT_RE.sub("", text)
    text = LINE_COMMENT_RE.sub("", text)
    return STRING_RE.sub("''", text)


def strip_comments(text: str) -> str:
    """剔除注释与字符串字面量。

    `.uvue` **只对 `<script>` 区段**做 JS 剔除：模板里的属性值是**代码**（如
    `:class="customChipCls(c)"`），若对模板也套用字符串规则，会因模板内引号配对而**吞掉大段**
    内容 ⇒ 漏报（实测：4 个模板助手名被吞掉）——漏报方向比误报更危险，故模板区段只剔 HTML 注释。
    """
    text = HTML_COMMENT_RE.sub("", text)
    if "<script" in text:
        return re.sub(r"<script[\s\S]*?</script>", lambda m: _strip_js(m.group(0)), text)
    return _strip_js(text)


def iter_client_sources():
    for p in CLIENT.rglob("*"):
        if p.suffix not in (".uvue", ".uts", ".ts") or not p.is_file():
            continue
        if any(s in p.parts for s in SKIP_DIRS):
            continue
        yield p


def module_exports() -> dict[str, list[str]]:
    out: dict[str, list[str]] = {}
    for d in MODULE_DIRS:
        if not d.exists():
            continue
        for p in d.rglob("*"):
            if p.suffix not in (".uts", ".ts") or not p.is_file():
                continue
            if any(s in p.parts for s in SKIP_DIRS):
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            names = set(EXPORT_RE.findall(text))
            for grp in EXPORT_LIST_RE.findall(text):
                for part in grp.split(","):
                    n = part.strip().split(" as ")[-1].strip()
                    if n:
                        names.add(n)
            for n in names:
                out.setdefault(n, []).append(str(p.relative_to(ROOT)).replace("\\", "/"))
    return out


def scan() -> tuple[list[dict], int]:
    """返回 (违规列表, 扫描的导出符号数)。供本脚本 CLI 与 `audit_harness` 轴共用。"""
    exports = module_exports()
    violations: list[dict] = []
    for p in iter_client_sources():
        if any(s in p.parts for s in SKIP_DIRS):
            continue
        # 模块自身不算（它就是定义处）
        if p in list(MODULE_DIRS[0].rglob("*")) or p in list(MODULE_DIRS[1].rglob("*")):
            continue
        text = p.read_text(encoding="utf-8", errors="replace")
        code = strip_comments(text)
        imported = " ".join(IMPORT_BLOCK_RE.findall(text))
        for name, homes in exports.items():
            if not re.search(USE_TPL.format(name=re.escape(name)), code):
                continue
            if re.search(DECL_RE_TPL.format(name=re.escape(name)), code, re.M):
                continue  # 本地声明同名 → 跳过
            if re.search(USE_TPL.format(name=re.escape(name)), imported):
                continue  # 已在某条 import 中出现
            violations.append({
                "file": str(p.relative_to(ROOT)).replace("\\", "/"),
                "symbol": name,
                "declared_in": homes,
            })
    return violations, len(exports)


def main() -> int:
    violations, n_exports = scan()
    payload = {"tool": "audit_client_imports", "passed": not violations,
               "violations": violations, "checked_modules": n_exports}
    out = None
    for i, a in enumerate(sys.argv):
        if a == "--json" and i + 1 < len(sys.argv):
            out = Path(sys.argv[i + 1])
    if out is not None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if violations:
        print(f"[CRITICAL] {len(violations)} 处「用了客户端模块导出符号但未 import」：")
        for v in violations:
            print(f"  - {v['file']}: {v['symbol']}（定义于 {', '.join(v['declared_in'])}）")
        return 1
    print(f"[OK] 客户端模块导出符号使用均有 import（扫描导出 {n_exports} 个）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
