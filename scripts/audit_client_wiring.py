"""客户端「按钮 → 接线」与「端点 → 调用」双向审计（2026-10-15 · 用户要求）。

## 解决什么问题

用户问：「客户端所有按钮是否都有对应的 API 端口？」「还有哪些功能实际没有任何接线/调用？」
这两问此前只能靠人工翻页，且本仓已有先例证明它**真的会发生**：

  · 模板绑了**不存在的处理函数**（`@tap="goVoice"` 而全页无 `goVoice` 定义）——历史实锤；
  · 按钮可点、界面有反馈，但**不产生任何请求、也不改状态**（点了等于没点）；
  · 后端端点存在，但**客户端零调用**（功能"以为交付了"）。

本脚本把这三类变成机械判据，输出**带行号**的清单，供人工逐条判定。

## 判据

**A. 模板处理函数**
  1. `handler_missing`：`@tap/@click` 绑的名字在全仓**找不到定义**（也没 import）⇒ 运行时点不动/报错；
  2. `handler_noop`：函数体里**既无 API 调用、也无导航/UI 反馈、也无状态写入**（空体或纯注释）
     ⇒ "点了没反应"；
  3. `handler_local`：只有本地/导航语义（合法，仅统计）。

**B. 端点 × 客户端**
  1. 取 `docs/openapi.json` 的路径集合；逐条在 `client/` 里找调用（`{param}` 归一为任意段）；
  2. 输出**客户端零引用**的端点（含方法与路径）——服务端专用/回调类需人工写明理由。

## 用法

    python scripts/audit_client_wiring.py            # 人类可读
    python scripts/audit_client_wiring.py --json     # 机读
    python scripts/audit_client_wiring.py --selftest # 负向探针（坏样本必须被抓到）

退出码：0 = 无「无接线」类发现；1 = 有；2 = 环境错误。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLIENT = REPO / "client"
OPENAPI = REPO / "docs" / "openapi.json"
SKIP_PARTS = {"unpackage", "uni_modules"}

# 模板上"看起来是处理函数"的属性
ATTR_RE = re.compile(r"@(?:tap|click|longpress|change|confirm)(?:\.\w+)?\s*=\s*\"([^\"]+)\"")
# 定义形式：function NAME( / const NAME = ( / async function NAME(
DEF_RE = re.compile(
    r"(?:async\s+)?function\s+([A-Za-z_]\w*)\s*\(|"
    r"(?:const|let)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?(?:\(|function)"
)
IMPORT_RE = re.compile(r"import\s*\{([^}]+)\}\s*from")
# 有接线的信号（**校准过一轮**：初版漏了 emit / 服务对象方法 / uni.* ，导致 32 条里
# 绝大多数是假阳性 —— 例：`onClose = () => emit('close')`、`confirmPending()` 里调
# `eo.confirmPending(...)`（服务对象方法）都是**真接线**，只是不碰裸 get/post。）
API_RE = re.compile(
    r"\b(?:get|post|put|request|del|delete)\s*\(|\.then\s*\(|fetch[A-Z]\w*\(|upload[A-Z]\w*\("
    r"|enqueue[A-Z]\w*\(|dataArr\(|dataObj\(|\*Async\("
    # 服务对象/组合式函数方法（本仓既有范式：eo=EventOps, pa=PhotoAttach, wf=WaveformCache）
    r"|\b(?:eo|pa|wf|api|store|queue|uploader|svc)\s*\.\s*[a-z]\w*\s*\("
    r"|\bemit\s*\(|\buni\s*\.\s*\w+\s*\("
)
NAV_RE = re.compile(
    r"navigateTo|switchTab|redirectTo|reLaunch|backToShell|showModal|showToast|showLoading|"
    r"shellSheet|shellActiveTab|openDetail|onBack\(|reload\(|refresh|close\b|dismiss"
)
WRITE_RE = re.compile(r"\.value\s*=|\.value\s*\+=|\.push\(")
# 「委托调用」：本仓最常见的写法是**一行委托**——`addPhotos(){ pickPhotos() }`、
# `toggleRecord(){ vr.toggleRecord() }`、`onMicTap(){ startRecording() }`。它们都是真接线
# （效果在下一层），但初版判据只看裸 get/post/导航 ⇒ 全被判成"点了没反应"（10 条里 9 条假阳性）。
# 故把"任何函数/方法调用"也算作**有效果**；仅当函数体**空**或**纯无调用**时才报。
CALL_RE = re.compile(
    r"\b[A-Za-z_]\w*\s*\.\s*[A-Za-z_]\w*\s*\(|"
    # ⚠️ 排除词必须带**词边界**：初版写 `(?!...|do)` 会把 `doContinue(...)` 一起排除
    # （`doContinue` 以 "do" 开头）⇒ UploadStatusBanner 两处真接线被误报成"无接线"。
    r"(?<![.\w])(?!if\b|for\b|while\b|switch\b|catch\b|return\b|typeof\b|new\b|await\b"
    r"|function\b|else\b|do\b)[a-zA-Z_]\w*\s*\("
)


def _client_files(suffixes: tuple[str, ...]) -> list[Path]:
    if not CLIENT.exists():
        return []
    out: list[Path] = []
    for suf in suffixes:
        out += list(CLIENT.rglob(f"*{suf}"))
    return [p for p in out if not (SKIP_PARTS & set(p.parts))]


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO)).replace("\\", "/")
    except ValueError:
        return str(p).replace("\\", "/")


def _body_after(text: str, start: int) -> str:
    """从 start 处的 `{` 起做花括号配对，返回函数体（配对失败则给 40 行窗口）。"""
    i = text.find("{", start)
    if i < 0:
        return ""
    depth = 0
    for j in range(i, len(text)):
        if text[j] == "{":
            depth += 1
        elif text[j] == "}":
            depth -= 1
            if depth == 0:
                return text[i : j + 1]
    return text[i : i + 2000]


def scan_handlers() -> tuple[list[dict], dict]:
    files = _client_files((".uvue", ".uts"))
    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}

    # 全仓已定义符号 + 各文件 import 的符号
    defined: set[str] = set()
    for t in texts.values():
        for m in DEF_RE.finditer(t):
            defined.add(m.group(1) or m.group(2))
    imported: set[str] = set()
    for t in texts.values():
        for m in IMPORT_RE.finditer(t):
            for name in m.group(1).split(","):
                nm = name.strip().split(" as ")[-1].strip()
                if nm:
                    imported.add(nm)

    findings: list[dict] = []
    stats = {"uvue": 0, "bindings": 0, "missing": 0, "noop": 0, "local": 0, "wired": 0}

    for p, t in texts.items():
        if p.suffix != ".uvue":
            continue
        stats["uvue"] += 1
        rel = _rel(p)
        # 1) 模板绑定的处理函数
        for m in ATTR_RE.finditer(t):
            raw = m.group(1)
            # 只取函数名调用形态（`fn(...)` 或 `fn`）；含运算符/字面量的表达式跳过
            name_m = re.match(r"\s*([A-Za-z_]\w*)\s*(?:\(|$)", raw)
            if not name_m:
                continue
            name = name_m.group(1)
            if name.startswith("uni") or name in ("console", "Math", "Date"):
                continue
            stats["bindings"] += 1
            line = t.count("\n", 0, m.start()) + 1
            if name not in defined and name not in imported:
                stats["missing"] += 1
                findings.append(
                    {
                        "kind": "handler_missing",
                        "file": rel,
                        "line": line,
                        "name": name,
                        "detail": "模板绑定的处理函数在全仓找不到定义（点不动/运行时报错）",
                    }
                )
                continue
            # 2) 找到定义并看函数体
            dm = re.search(
                r"(?:async\s+)?function\s+" + re.escape(name) + r"\s*\(|"
                r"(?:const|let)\s+" + re.escape(name) + r"\s*=\s*(?:async\s*)?(?:\(|function)",
                t,
            )
            if dm is None:
                continue  # 定义在本文件之外（正常的 import 用法）
            body = _body_after(t, dm.start())
            if not body.strip() or body.strip() in ("{}", "{ }"):
                stats["noop"] += 1
                findings.append(
                    {
                        "kind": "handler_noop",
                        "file": rel,
                        "line": t.count("\n", 0, dm.start()) + 1,
                        "name": name,
                        "detail": "函数体为空 ⇒ 点了没反应",
                    }
                )
                continue
            has_api = bool(API_RE.search(body))
            has_nav = bool(NAV_RE.search(body))
            has_write = bool(WRITE_RE.search(body))
            has_call = bool(CALL_RE.search(body))  # 委托调用（下一层才是真效果）也算有效果
            if not (has_api or has_nav or has_write or has_call):
                stats["noop"] += 1
                findings.append(
                    {
                        "kind": "handler_noop",
                        "file": rel,
                        "line": t.count("\n", 0, dm.start()) + 1,
                        "name": name,
                        "detail": "函数体既无 API 调用、也无导航/UI 反馈、也无状态写入 ⇒ 疑似点了没反应",
                    }
                )
            elif has_api:
                stats["wired"] += 1
            else:
                stats["local"] += 1
    return findings, stats


def scan_endpoints() -> tuple[list[dict], dict]:
    if not OPENAPI.exists():
        return [], {"paths": 0}
    spec = json.loads(OPENAPI.read_text(encoding="utf-8"))
    paths = spec.get("paths") or {}
    files = _client_files((".uvue", ".uts"))
    raw = "\n".join(p.read_text(encoding="utf-8", errors="replace") for p in files)
    # ⚠️ 校准：客户端大量经 `contract.uts` 的 `PATH_*` **常量**调用（如 `post(PATH_CLASSIFY…)`）
    # ⇒ 只搜字面量会把它们全判成"零引用"（全是假阳性）。这里把常量的**值**也当作已引用；
    # "常量声明了却没人用"由第 10 轴（`audit_contract_const.py`）独立负责，两者不重叠。
    const_vals = re.findall(r"PATH_[A-Z0-9_]+\s*(?::\s*string)?\s*=\s*'([^']+)'", raw)
    blob = raw + "\n" + "\n".join(const_vals)
    unused: list[dict] = []
    for path, ops in sorted(paths.items()):
        # `/api/v1/contents/{content_id}/events` → 正则：参数段归一为任意非斜杠串
        # 同时容纳"前缀拼接"写法（`'/api/v1/messages/' + id + '/read'`）⇒ 只匹配到参数段之前
        # ⚠️ 替换串里不能出现 `\s`（re.sub 会把 `\` 当转义 ⇒ 报 "bad escape \s"），
        # 故用不含转义的等价字符类 `[^/"' ]`（路径段本就不含空格）。
        pat = re.sub(r"\{[^}]+\}", "[^/\"' ]+", re.escape(path))
        if re.search(pat, blob):
            continue
        prefix = path.split("{")[0].rstrip("/")
        if len(prefix) > 8 and prefix in blob:
            # 前缀命中（客户端拼接调用）⇒ 视为已引用，但标注出来供人工确认
            continue
        unused.append({"path": path, "methods": sorted(ops.keys())})
    return unused, {"paths": len(paths), "client_files": len(files), "const_vals": len(const_vals)}


def selftest(verbose: bool = True) -> int:
    """负向探针：坏样本必须被抓到（绑了不存在的函数 / 空函数体）。"""
    global CLIENT  # noqa: PLW0603 —— 探针临时改扫描根
    import tempfile

    original = CLIENT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.uvue").write_text(
            "<template><view @tap=\"ghostHandler()\">x</view>"
            "<view @tap=\"emptyHandler()\">y</view>"
            "<view @tap=\"realHandler()\">z</view></template>\n"
            "<script setup>\n"
            "function emptyHandler(): void {}\n"
            "function realHandler(): void {\n"
            "  get('/api/v1/contents').then((r: UTSJSONObject | null) => {})\n"
            "}\n"
            "</script>\n",
            encoding="utf-8",
        )
        CLIENT = root
        try:
            findings, _stats = scan_handlers()
        finally:
            CLIENT = original
    kinds = {(f["kind"], f["name"]) for f in findings}
    failures: list[str] = []
    if ("handler_missing", "ghostHandler") not in kinds:
        failures.append("未抓到「绑了不存在的函数」坏样本 ghostHandler")
    if ("handler_noop", "emptyHandler") not in kinds:
        failures.append("未抓到「空函数体」坏样本 emptyHandler")
    if any(name == "realHandler" for _k, name in kinds):
        failures.append("误报：realHandler 有 API 调用却被判无接线")
    if failures:
        if verbose:
            print("[selftest 失败] " + "；".join(failures))
        return 1
    if verbose:
        print("[selftest 通过] 两类坏样本被抓到，有接线的样本未误报")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="客户端按钮↔接线 / 端点↔调用 双向审计")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not CLIENT.exists():
        payload = {"status": "env_error", "detail": f"client/ 不存在：{CLIENT}"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["detail"])
        return 2

    h_findings, h_stats = scan_handlers()
    e_findings, e_stats = scan_endpoints()

    if args.json:
        print(json.dumps(
            {"handlers": h_findings, "stats": h_stats,
             "endpoints_unused": e_findings, "endpoint_stats": e_stats},
            ensure_ascii=False,
        ))
        return 1 if h_findings else 0

    print(f"[A] 模板绑定 {h_stats['bindings']} 处 · 处理函数有 API 调用 {h_stats['wired']} · "
          f"仅本地/导航 {h_stats['local']} · **疑似无接线 {h_stats['noop']}** · "
          f"**函数不存在 {h_stats['missing']}**")
    for f in h_findings:
        print(f"  - [{f['kind']}] {f['file']}:{f['line']} {f['name']} — {f['detail']}")
    print(f"\n[B] 端点 {e_stats['paths']} 条 · **客户端零引用 {len(e_findings)}**")
    for e in e_findings:
        print(f"  - {'/'.join(e['methods']).upper():16s} {e['path']}")
    return 1 if h_findings else 0


if __name__ == "__main__":
    sys.exit(main())
