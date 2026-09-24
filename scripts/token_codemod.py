"""设计令牌 codemod（令牌波 P3 的执行器 · 2026-09-25）。

用途：把 `client/**` 样式里的**硬编码字面量**改写为 `var(--令牌, 原字面量)`。

为什么带**回退值**：uni-app x App 端「样式不继承」是实锤，`var()` 的**运行期解析**在本项目尚未经真机确认
（P2 只证到"编译透传 + 产物含声明与取值"）。带原字面量回退 ⇒ 变量解析与否**视觉都零变化** ⇒
在没有设备时也能安全推进 P3，且任何一步都可单独 revert。

命名（deterministic，不猜角色）：
  · 颜色 primitive      `color.ink.900`        → `--c-ink-900`
  · 透明阶梯            `alpha.ink.a05`        → `--a-ink-05`
  · 尺寸画布格          `dimension.lattice.p12`→ `--d-p12`
  · 尺寸 rpx 原生整数   `dimension.raw.r24`    → `--d-r24`
  · 渐变                `gradient.veil-ink-ink`→ `--g-veil-ink-ink`
  · 效果                `effect.shadow-card`   → `--e-shadow-card`
  · 字体                `font.sans` / `font.weight.semibold` → `--f-sans` / `--f-weight-semibold`
> **刻意不按"用途"命名**（text_primary 之类）：同一字面量可对应多个语义，按用途命名需要"调用点角色"信息，
> 那是 P3 之后的**精修**（semantic 层已在令牌表里备好，届时按元素角色替换即可）。

颜色归一（关键，否则大量命中不到）：
  · 一切颜色都过 `norm_color()` → `transparent`（α=0）/ `#rrggbb`（α=1）/ `rgba(r,g,b,a)`（α 四舍五入 2 位）
    ⇒ 于是 `#ffffff8c`、`rgba(248,247,244,0)`、`rgba(196,145,60,0.10)`、`rgba(58,46,37,0.078)` 都能对上号；
  · 同族 1~2/255 漂移（`#3b2e26`、`rgba(59,46,38,α)` 等）**归并到规范值**——既定"视觉等效、字节不等"口径。
  · 渐变：两侧都剔除停靠点百分比后比对。

用法：
  python scripts/token_codemod.py --plan [--only <相对路径子串>]   # 只出计划（默认；不改任何文件）
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

from audit_token_literals import _strip_var_fallbacks  # noqa: E402

force_utf8()

ROOT = Path(__file__).resolve().parent.parent
CLIENT = ROOT / "client"
TOKENS = CLIENT / "design_tokens.dtcg.json"
SKIP_DIRS = ("uni_modules", "unpackage", "node_modules", ".git")

COLOR_RE = re.compile(r"#[0-9a-fA-F]{3,8}\b|rgba?\([^)]*\)")
RATIO_RE = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(rpx|px)\b")
GRAD_RE = re.compile(r"linear-gradient\([^)]*\)")

# 同族 1~2/255 漂移 → 规范值（与令牌表 $description 的记载一致）
HEX_MERGE = {
    "#3b2e26": "#3a2e25", "#b2a696": "#b3a696", "#b0593b": "#b05a3a", "#af5a39": "#b05a3a", "#aa5334": "#b05a3a",
    "#f6efe3": "#f6f1e7", "#faf6ee": "#f6f1e7", "#fbf7ef": "#f6f1e7", "#fbf4e4": "#f6f1e7",
    "#f0ebe2": "#f0ebe3", "#efeae2": "#f0ebe3", "#efe9dd": "#f0ebe3",
    "#ede4d4": "#ede5d5", "#ede5d8": "#ede5d5", "#ebe3d4": "#ede5d5",
    "#e5ded4": "#e5dcc8", "#e5ded2": "#e5dcc8", "#d8cfc2": "#d8d0c6",
}
RGB_MERGE = {
    (59, 46, 38): (58, 46, 37), (42, 33, 26): (58, 46, 37),
    (176, 89, 59): (176, 90, 58), (176, 89, 58): (176, 90, 58),
    (196, 145, 59): (196, 145, 60), (25, 20, 15): (26, 20, 15),
}


def norm_color(s: str) -> str | None:
    """任意颜色写法 → 规范键（transparent / #rrggbb / rgba(r,g,b,a)）。"""
    s = re.sub(r"\s+", "", s)
    m = re.match(r"rgba\((\d+),(\d+),(\d+),([\d.]+)\)$", s)
    if m:
        r, g, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
        a = round(float(m.group(4)), 2)
    else:
        m = re.match(r"rgb\((\d+),(\d+),(\d+)\)$", s)
        if m:
            r, g, b, a = int(m.group(1)), int(m.group(2)), int(m.group(3)), 1.0
        elif re.fullmatch(r"#[0-9a-fA-F]{3}", s):
            h = "".join(c * 2 for c in s[1:])
            r, g, b, a = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), 1.0
        elif re.fullmatch(r"#[0-9a-fA-F]{6}", s):
            r, g, b, a = int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16), 1.0
        elif re.fullmatch(r"#[0-9a-fA-F]{8}", s):
            r, g, b = int(s[1:3], 16), int(s[3:5], 16), int(s[5:7], 16)
            a = round(int(s[7:9], 16) / 255, 2)
        else:
            return None
    if a == 0:
        return "transparent"
    if a == 1:
        return f"#{r:02x}{g:02x}{b:02x}"
    return f"rgba({r},{g},{b},{a})"


def norm_grad(s: str) -> str:
    return re.sub(r"\s+", "", re.sub(r"\s*\d+(?:\.\d+)?%", "", s))


def find_gradients(code: str) -> list[str]:
    """括号配平地取 `linear-gradient(...)` 全文。

    ⚠️ 不能用 `linear-gradient\\([^)]*\\)`：梯度里几乎必含 `rgba(...)`，字符类会在**内层 `)`** 处截断
    （本轮实测：截断后 10 条梯度只有 1 条能对上号）。
    """
    out: list[str] = []
    i = 0
    while True:
        k = code.find("linear-gradient(", i)
        if k == -1:
            return out
        j = k + len("linear-gradient(")
        depth = 1
        while j < len(code) and depth:
            if code[j] == "(":
                depth += 1
            elif code[j] == ")":
                depth -= 1
            j += 1
        out.append(code[k:j])
        i = j


def _load():
    return json.loads(TOKENS.read_text(encoding="utf-8"))


def _leaves(node, prefix=""):
    if isinstance(node, dict):
        if "$value" in node:
            yield prefix, node["$value"]
            return
        for k, v in node.items():
            if not k.startswith("$"):
                yield from _leaves(v, f"{prefix}.{k}" if prefix else k)


def _resolve(T, val):
    seen = 0
    while isinstance(val, str) and val.startswith("{") and seen < 20:
        node = T
        for seg in val[1:-1].split("."):
            node = node[seg]
        val = node["$value"]
        seen += 1
    return val


def var_name(path: str) -> str:
    head, _, rest = path.partition(".")
    if head == "color":
        return "--c-" + rest.replace(".", "-")
    if head == "alpha":
        fam, _, step = rest.partition(".")
        return f"--a-{fam}-{step[1:]}"
    if head == "dimension":
        return "--d-" + rest.replace(".", "-")
    if head == "gradient":
        return "--g-" + rest.replace(".", "-")
    if head == "effect":
        return "--e-" + rest.replace(".", "-")
    if head == "font":
        return "--f-" + rest.replace(".", "-")
    return "--x-" + path.replace(".", "-")


def build_index(T):
    """产出 (颜色规范键→var, 尺寸原样→var, 渐变规范→var, 归并记录)。"""
    colors: dict[str, str] = {}
    dims: dict[str, str] = {}
    grads: dict[str, str] = {}
    for path, raw in _leaves(T):
        v = _resolve(T, raw)
        name = var_name(path)
        if not isinstance(v, str):
            continue
        if v == "transparent":
            colors.setdefault("transparent", name)
        elif v.startswith("linear-gradient("):
            grads.setdefault(norm_grad(v), name)
        elif v.endswith(("rpx", "px")) and re.fullmatch(r"[\d.]+(rpx|px)", v):
            dims.setdefault(v, name)
        else:
            k = norm_color(v)
            if k is not None:
                colors.setdefault(k, name)
    merged: list[str] = []
    for src, dst in HEX_MERGE.items():
        k = norm_color(dst)
        if k in colors and norm_color(src) not in colors:
            colors[norm_color(src)] = colors[k]
            merged.append(f"{src} → {dst}（{colors[k]}）")
    ALPHA_STEPS = ("0.04", "0.05", "0.06", "0.08", "0.1", "0.12", "0.14", "0.15", "0.16", "0.18",
                   "0.2", "0.25", "0.28", "0.3", "0.35", "0.4", "0.45", "0.5", "0.55", "0.6",
                   "0.65", "0.7", "0.8", "0.85")
    for src, dst in RGB_MERGE.items():
        for a in ALPHA_STEPS:
            kk = f"rgba({dst[0]},{dst[1]},{dst[2]},{a})"
            sk = f"rgba({src[0]},{src[1]},{src[2]},{a})"
            if kk in colors and sk not in colors:
                colors[sk] = colors[kk]
                merged.append(f"{sk} → {kk}（{colors[kk]}）")
    return colors, dims, grads, merged


def _iter_sources():
    for ext in ("*.uvue", "*.css"):
        for p in CLIENT.rglob(ext):
            if p.is_file() and not any(s in p.parts for s in SKIP_DIRS):
                yield p


GEN_START = "/* >>> token-wave:generated:start"
GEN_END = "/* <<< token-wave:generated:end"


def style_scope(text: str, suffix: str) -> tuple[int, int]:
    """可改写区间：`.css` 取全文；`.uvue` **只取 `<style>` 区**（模板里的行内 style 属 P5 单独一簇）。"""
    if suffix == ".css":
        return 0, len(text)
    m = re.search(r"<style[^>]*>", text)
    if not m:
        return 0, 0
    e = text.rfind("</style>")
    return m.end(), (e if e > m.end() else len(text))


def _signed(scope: str, i: int) -> bool:
    """该位置是否处于**符号**之后（`-46rpx` / `- 46rpx`）。

    ⚠️ 必须跳过：把 `-46rpx` 改写成 `-var(--d-r46, 46rpx)` 是**非法 CSS**（值是 `-var(...)`），
    声明会被丢弃 ⇒ 直接造成视觉回归（实测批次①当场撞到 `margin-left: -46rpx`）。
    """
    j = i - 1
    while j >= 0 and scope[j] in " \t\r\n":
        j -= 1
    return j >= 0 and scope[j] in "-+"


def rewrite_scope(scope: str, colors, dims, grads) -> tuple[str, int]:
    """把区间内的字面量改写为 `var(--令牌, 原字面量)`。

    逐位置扫描：**注释**与**已有的 `var(...)`** 整段原样复制（避免在注释里误改、避免二次包裹）。
    """
    out: list[str] = []
    i = 0
    n = 0
    while i < len(scope):
        if scope.startswith("/*", i):
            j = scope.find("*/", i + 2)
            j = len(scope) if j == -1 else j + 2
            out.append(scope[i:j])
            i = j
            continue
        if scope.startswith("var(", i):
            j = i + 4
            depth = 1
            while j < len(scope) and depth:
                if scope[j] == "(":
                    depth += 1
                elif scope[j] == ")":
                    depth -= 1
                j += 1
            out.append(scope[i:j])
            i = j
            continue
        if scope.startswith("linear-gradient(", i):
            j = i + len("linear-gradient(")
            depth = 1
            while j < len(scope) and depth:
                if scope[j] == "(":
                    depth += 1
                elif scope[j] == ")":
                    depth -= 1
                j += 1
            lit = scope[i:j]
            name = grads.get(norm_grad(lit))
            if name:
                out.append(f"var({name}, {lit})")
                n += 1
                i = j
                continue
        m = COLOR_RE.match(scope, i)
        if m:
            lit = m.group(0)
            k = norm_color(lit)
            name = colors.get(k) if k else None
            if name:
                out.append(f"var({name}, {lit})")
                n += 1
                i = m.end()
                continue
        m = RATIO_RE.match(scope, i)
        if m and not _signed(scope, i):
            lit = f"{m.group(1)}{m.group(2)}"
            name = dims.get(lit)
            if name:
                out.append(f"var({name}, {lit})")
                n += 1
                i = m.end()
                continue
        out.append(scope[i])
        i += 1
    return "".join(out), n


def emit_block(T) -> str:
    """生成声明块（带生成区标记）。**所有**令牌都声明一次，供各页/组件引用。"""
    lines = [f"\t{GEN_START}（由 scripts/token_codemod.py 从 design_tokens.dtcg.json 生成，勿手工编辑） */",
             "\t.tk-root {"]
    for path, raw in sorted(_leaves(T)):
        lines.append(f"\t\t{var_name(path)}: {_resolve(T, raw)};")
    lines += ["\t}", f"\t{GEN_END} */"]
    return "\n".join(lines) + "\n"


def update_app_block(text: str, block: str) -> str:
    """把 App.uvue 的生成区替换为 block（首次则插到 `</style>` 前）。"""
    k = text.find(GEN_START)
    if k != -1:
        e = text.find("\n", text.find(GEN_END, k))
        return text[:k] + block.lstrip("\t") + text[e + 1 :]
    i = text.rfind("</style>")
    return text[:i] + block + text[i:]


def ensure_root_class(text: str) -> tuple[str, bool]:
    """给 `.uvue` 的**首个根元素**挂 `tk-root`（变量声明的作用载体）。"""
    if re.search(r'class="[^"]*\btk-root\b', text):
        return text, False
    m = re.search(r'<view\s+class="([^"]*)"', text)
    if not m:
        return text, False
    return text[: m.start(1)] + m.group(1) + " tk-root" + text[m.end(1) :], True


def plan(only: str | None = None) -> dict:
    T = _load()
    colors, dims, grads, merged = build_index(T)
    res: dict = {"files": {}, "residual": [], "merged": merged}
    for p in _iter_sources():
        rel = p.relative_to(ROOT).as_posix()
        if only and only not in rel:
            continue
        raw = p.read_text(encoding="utf-8", errors="replace")
        s, e = style_scope(raw, p.suffix)
        code = _strip_var_fallbacks(strip_comments(raw[s:e]))
        hits = {"hex": 0, "rgb": 0, "dim": 0, "gradient": 0}
        for c in COLOR_RE.findall(code):
            k = norm_color(c)
            if k is not None and k in colors:
                hits["rgb" if k.startswith("rgba") else "hex"] += 1
            else:
                res["residual"].append((rel, c))
        for m in RATIO_RE.finditer(code):
            if f"{m.group(1)}{m.group(2)}" in dims:
                hits["dim"] += 1
            else:
                res["residual"].append((rel, f"{m.group(1)}{m.group(2)}"))
        for g in find_gradients(code):
            if norm_grad(g) in grads:
                hits["gradient"] += 1
            else:
                res["residual"].append((rel, "gradient"))
        if any(hits.values()):
            res["files"][rel] = hits
    return res


def _non_generated_ranges(scope: str) -> list[tuple[int, int]]:
    """区间内**排除生成区**的子区间列表。

    ⚠️ 必须排除：生成区是写器自己的产物（`--x: #abc;`），若被再改一次会变成
    `--x: var(--x, #abc);` —— **自引用环**，该自定义属性在计算期失效 ⇒ 令牌整体形同虚设。
    """
    out: list[tuple[int, int]] = []
    i = 0
    while True:
        k = scope.find(GEN_START, i)
        if k == -1:
            out.append((i, len(scope)))
            return out
        if k > i:
            out.append((i, k))
        e = scope.find(GEN_END, k)
        i = len(scope) if e == -1 else scope.find("\n", e) + 1 or len(scope)


def write_files(files, colors, dims, grads) -> int:
    """对给定相对路径逐个改写（`.uvue` 同时挂根类 + 写前自检）。返回改写总处数。"""
    tot = 0
    for rel in files:
        p = ROOT / rel
        t = p.read_text(encoding="utf-8")
        s, e = style_scope(t, p.suffix)
        scope = t[s:e]
        parts: list[str] = []
        n = 0
        cur = 0
        for a, b in _non_generated_ranges(scope):
            parts.append(scope[cur:a])            # 生成区原样保留
            new, k = rewrite_scope(scope[a:b], colors, dims, grads)
            parts.append(new)
            n += k
            cur = b
        parts.append(scope[cur:])
        t2 = t[:s] + "".join(parts) + t[e:]
        bad = re.findall(r"[-+]var\(", t2)
        if bad:
            print(f"[FAIL] {rel}: 改写后出现非法 `-var(`/`+var(` {len(bad)} 处 —— **未写入**，请检查写器")
            continue
        line = f"[write] {rel}: 改写 {n} 处"
        if p.suffix == ".uvue":
            t2, added = ensure_root_class(t2)
            if added:
                line += " · 根节点已挂 tk-root"
        p.write_text(t2, encoding="utf-8")
        print(line)
        tot += n
    return tot


def main() -> int:
    args = sys.argv[1:]
    T = _load()
    colors, dims, grads, _merged = build_index(T)
    if "--write-block" in args:
        app = CLIENT / "App.uvue"
        app.write_text(update_app_block(app.read_text(encoding="utf-8"), emit_block(T)), encoding="utf-8")
        print("[write-block] App.uvue 的生成区已刷新（全部令牌声明一次）")
        return 0
    if "--write-uvue-all" in args:
        files = [f for f in plan().get("files", {}) if f.endswith(".uvue")]
        print(f"[write-uvue-all] 目标 {len(files)} 个 .uvue")
        print(f"[write] 合计改写 {write_files(files, colors, dims, grads)} 处")
        return 0
    if "--write" in args:
        files = [args[i + 1] for i, a in enumerate(args) if a == "--file" and i + 1 < len(args)]
        tot = write_files(files, colors, dims, grads)
        class_on = args[args.index("--class-on") + 1] if "--class-on" in args else None
        if class_on:
            p = ROOT / class_on
            t2, added = ensure_root_class(p.read_text(encoding="utf-8"))
            if added:
                p.write_text(t2, encoding="utf-8")
            print(f"[write] {class_on}: {'根节点已挂 tk-root' if added else '已含 tk-root（跳过）'}")
        print(f"[write] 合计改写 {tot} 处")
        return 0
    only = args[args.index("--only") + 1] if "--only" in args else None
    r = plan(only)
    tot = {k: sum(v[k] for v in r["files"].values()) for k in ("hex", "rgb", "dim", "gradient")}
    print("== P3 改写计划（dry-run，未改动任何文件）==")
    print(f"可改写处数：hex {tot['hex']} · rgb {tot['rgb']} · 尺寸 {tot['dim']} · 渐变 {tot['gradient']}"
          f"（合计 {sum(tot.values())}）")
    print(f"涉及文件：{len(r['files'])} 个 · 归并记录：{len(r['merged'])} 条")
    for m in r["merged"][:6]:
        print("   -", m)
    agg: dict[str, int] = {}
    for _rel, lit in r["residual"]:
        agg[lit] = agg.get(lit, 0) + 1
    print(f"残余不可映射：{len(agg)} 种 / {sum(agg.values())} 处")
    for lit, n in sorted(agg.items(), key=lambda kv: -kv[1])[:12]:
        print(f"   - {lit} ×{n}")
    print("Top 8 文件：")
    for rel, h in sorted(r["files"].items(), key=lambda kv: -sum(kv[1].values()))[:8]:
        print(f"   {sum(h.values()):5d}  {rel}  {h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
