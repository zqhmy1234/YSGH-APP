#!/usr/bin/env python3
"""harness 三轴审计（契约漂移 / 客户端漂移 / 模型使用率）

背景（2026-09-23 系统性审计，见 docs/审计_未关闭缺陷_20260923.md）：
  审计发现三类"静默漂移"从未被任何门禁覆盖，且已各自造成误导：
  1. **契约漂移**：`docs/openapi.json` 的再生是**人工步骤**（既无脚本、也无 CI 断言），
     实测已积压 17 条路径整月无人发现；本轴做**双向**对拍（不只查"契约缺路径"，
     也查"契约里有、后端已无"）。
  2. **客户端漂移**：注释/文档里引用**已不存在的页面路径**、`USE_MOCK_*` 演示开关
     散落无清单（发版要求全 false 却无清点手段）、5.24 迁移后残留 `.ts` 引用。
     实测一次即抓到 4 处（shell.uvue 老四页注释 / search_api mock 叙述 /
     feature_list 自相矛盾 / client/README 陈旧目录树）。
  3. **模型使用率**：`app/db/models/` 定义的 ORM 类是否有业务引用——"建了表但没人用"
     属静默腐化，出问题时才被发现。

方法：
  · openapi：本地 `from app.main import app` 实时导出 paths，与 `docs/openapi.json` 做
    路径集 + 方法集**双向** diff。导入失败（缺依赖/环境）时明确报出原因而非 crash。
  · client：① 扫 `USE_MOCK_*` 赋值并清点真值 ② 从 `client/pages.json` 取已注册页面集，
    再扫 `client/**` 的 `/pages/x/y` 字面量——**按是否落在注释里分流**：
    注释内＝历史标注（INFO），代码内＝**死路由（CRITICAL）**
    ③ 扫 `.ts` 后缀引用（5.24 迁移后应为 `.uts`）。
  · models：正则取 `class X(` 类名，统计其在 `backend/app/**`（排除 models 定义处）
    的出现次数；0 引用＝疑似未使用（**静态启发式，非结论**，ORM 字符串式引用需人工判）。

用法：
  python scripts/audit_harness.py openapi|client|models|all [-v]
  python scripts/audit_harness.py all --json out.json      # 供 CI/台账引用

退出码：
  0 = 全部无 CRITICAL 发现
  1 = 存在 CRITICAL（契约双向漂移有差 / 代码内死路由 / 模型 0 引用）
  2 = 执行环境错误（后端不可导入且非 --skip-openapi 等）
"""
from __future__ import annotations

import argparse
import contextlib
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 输出编码兜底：Windows 默认 GBK，`✓` 等符号会抛 UnicodeEncodeError 直接崩掉审计；
# 且 CI/本仓工具链按 UTF-8 解码管道输出。故统一按 UTF-8 输出，不可编码字符降级为 ?，
# 保证本脚本在任何终端/管道下都不会因编码问题失败。
for _stream in (sys.stdout, sys.stderr):
    # 无 reconfigure 的老环境降级为不动（ruff S110 不允许 try/except/pass，
    # 故用 contextlib.suppress 表达同一语义）
    with contextlib.suppress(Exception):
        _stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
CLIENT = REPO / "client"
BACKEND = REPO / "backend"
DOCS_OPENAPI = REPO / "docs" / "openapi.json"

CRITICAL: list[str] = []
INFO: list[str] = []


def _say(level: str, msg: str) -> None:
    print(f"  [{level}] {msg}")


def _crit(msg: str) -> None:
    CRITICAL.append(msg)
    _say("CRITICAL", msg)


def _info(msg: str) -> None:
    INFO.append(msg)
    _say("INFO", msg)


# ─────────────────────────── 轴 1：契约双向对拍 ───────────────────────────

def _local_openapi_paths() -> dict[str, set[str]] | None:
    """本地实时导出 openapi paths → {path: {methods}}；失败返回 None（原因已打印）"""
    sys.path.insert(0, str(BACKEND))
    try:
        from app.main import app  # type: ignore[import-not-found]
    except Exception as exc:  # noqa: BLE001 — 环境问题需原样报出
        _say("ERROR", f"无法导入 app.main（{type(exc).__name__}: {exc}）")
        _say("ERROR", "→ 契约轴需可导入的后端环境（缺依赖/环境变量）；可加 --skip-openapi 跳过")
        return None
    try:
        spec = app.openapi()
    except Exception as exc:  # noqa: BLE001
        _say("ERROR", f"app.openapi() 导出失败：{type(exc).__name__}: {exc}")
        return None
    out: dict[str, set[str]] = {}
    for path, item in spec.get("paths", {}).items():
        methods = {m.upper() for m in item if m.lower() in {"get", "post", "put", "patch", "delete"}}
        out[path] = methods
    return out


def _committed_openapi_paths() -> dict[str, set[str]] | None:
    if not DOCS_OPENAPI.exists():
        _say("ERROR", f"未找到 {DOCS_OPENAPI.relative_to(REPO)}")
        return None
    data = json.loads(DOCS_OPENAPI.read_text(encoding="utf-8"))
    out: dict[str, set[str]] = {}
    for path, item in data.get("paths", {}).items():
        methods = {m.upper() for m in item if m.lower() in {"get", "post", "put", "patch", "delete"}}
        out[path] = methods
    return out


def audit_openapi() -> None:
    print("== 轴 1 · 契约双向对拍（本地 app vs docs/openapi.json）==")
    local = _local_openapi_paths()
    committed = _committed_openapi_paths()
    if local is None or committed is None:
        raise SystemExit(2)

    _say("INFO", f"本地导出 {len(local)} 路径 / 契约快照 {len(committed)} 路径")

    only_local = sorted(set(local) - set(committed))
    only_committed = sorted(set(committed) - set(local))

    if only_local:
        _crit(f"后端有路由但契约缺（{len(only_local)} 条）→ 需重导 docs/openapi.json：")
        for p in only_local:
            _say("  ", f"{p}  [{', '.join(sorted(local[p]))}]")
    else:
        _info("后端路由全部已在契约中 ✓")

    if only_committed:
        _crit(f"契约有但后端已无（{len(only_committed)} 条）→ 契约积压/端点被删未同步：")
        for p in only_committed:
            _say("  ", f"{p}  [{', '.join(sorted(committed[p]))}]")
    else:
        _info("契约无幽灵路径 ✓")

    method_diff = []
    for p in sorted(set(local) & set(committed)):
        if local[p] != committed[p]:
            method_diff.append(f"{p}: 后端{sorted(local[p])} vs 契约{sorted(committed[p])}")
    if method_diff:
        _crit(f"同路径方法不一致（{len(method_diff)} 条）：")
        for d in method_diff:
            _say("  ", d)
    elif not only_local and not only_committed:
        _info("路径与方法集完全对齐 ✓")


# ─────────────────────────── 轴 2：客户端漂移 ───────────────────────────

CODE_SUFFIXES = {".uvue", ".uts", ".ts", ".js", ".json"}


def _client_sources() -> list[Path]:
    out: list[Path] = []
    for p in CLIENT.rglob("*"):
        if not p.is_file() or p.suffix not in CODE_SUFFIXES:
            continue
        if any(part in {"unpackage", "node_modules", ".hbuilderx"} for part in p.parts):
            continue
        out.append(p)
    return out


def _registered_pages() -> set[str]:
    pages_json = CLIENT / "pages.json"
    raw = pages_json.read_text(encoding="utf-8")
    # ⚠️ 归一化前导斜杠：pages.json 的 path 无斜杠（"pages/shell/shell"），
    # 而代码内引用带斜杠（"/pages/shell/shell"）——本工具初版漏此归一化，
    # 曾把全部 44 条正常跳转误报为「死路由」。凡路径比对必须先归一再比。
    return {p.lstrip("/") for p in re.findall(r'"path"\s*:\s*"([^"]+)"', raw)}


def audit_client() -> None:
    print("== 轴 2 · 客户端漂移（mock 开关 / 页面路径 / .ts 残留）==")
    sources = _client_sources()
    _say("INFO", f"扫描 {len(sources)} 个客户端源文件")

    # ① USE_MOCK_* 清点
    # ⚠️ 只认**声明形态**（`const X: boolean = true/false`）——初版按裸词匹配，
    # 会把注释里提到的开关名（如 TabAi 头注的示例）也算成开关，产生误报。
    mock_re = re.compile(
        r"\b(?:export\s+)?(?:const|let)\s+(USE_MOCK_[A-Z0-9_]+)\s*(?::\s*\w+)?\s*=\s*(true|false)"
    )
    mock_true: list[str] = []
    mock_false: list[str] = []
    for f in sources:
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in mock_re.finditer(text):
            line = text[: m.start()].count("\n") + 1
            name, val = m.group(1), m.group(2)
            entry = f"{f.relative_to(REPO)}:{line}  {name} = {val}"
            (mock_true if val == "true" else mock_false).append(entry)
    total_mock = len(mock_true) + len(mock_false)
    _say("INFO", f"USE_MOCK_* 共 {total_mock} 处：未关闭 {len(mock_true)} / 已关闭 {len(mock_false)}")
    for e in sorted(mock_true):
        _say("  ", e)
    if mock_true:
        _info(f"发版检查清单要求 USE_MOCK_* 全部 =false —— 当前仍有 {len(mock_true)} 处未关闭（见 B8 远期待办）")

    # ② 页面路径漂移（按是否在注释里分流）
    pages = _registered_pages()
    page_re = re.compile(r"/pages/[A-Za-z0-9_]+/[A-Za-z0-9_-]+")
    dead_code: list[str] = []
    comment_refs: list[str] = []
    for f in sources:
        if f.name == "pages.json":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in page_re.finditer(text):
            target = m.group(0)
            if target.lstrip("/") in pages:  # 两侧均归一化后比较（见 _registered_pages 注）
                continue
            start_of_line = text.rfind("\n", 0, m.start()) + 1
            prefix = text[start_of_line: m.start()]
            line = text[: m.start()].count("\n") + 1
            in_comment = ("//" in prefix) or ("<!--" in prefix)
            entry = f"{f.relative_to(REPO)}:{line}  {target}（未注册）"
            (comment_refs if in_comment else dead_code).append(entry)
    if dead_code:
        _crit(f"代码内引用未注册页面（死路由，{len(dead_code)} 条）：")
        for e in sorted(set(dead_code)):
            _say("  ", e)
    else:
        _info("无代码内死路由 ✓")
    if comment_refs:
        _info(f"注释内引用未注册页面（历史标注，建议标注「原」字样，{len(comment_refs)} 条）：")
        for e in sorted(set(comment_refs)):
            _say("  ", e)

    # ③ .ts 残留引用
    ts_re = re.compile(r"from\s+['\"][^'\"]+\.ts['\"]|['\"][^'\"]+\.ts['\"]\s*(?:,|\)|\})")
    ts_hits: list[str] = []
    for f in sources:
        if f.suffix not in {".uts", ".uvue", ".ts", ".json"}:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in ts_re.finditer(text):
            line = text[: m.start()].count("\n") + 1
            ts_hits.append(f"{f.relative_to(REPO)}:{line}  {m.group(0).strip()}")
    if ts_hits:
        _crit(f"残留 .ts 引用（5.24 迁移后应为 .uts，{len(ts_hits)} 条）：")
        for e in sorted(set(ts_hits)):
            _say("  ", e)
    else:
        _info("无 .ts 残留引用 ✓")


# ─────────────────────────── 轴 3：模型使用率 ───────────────────────────

def audit_models() -> None:
    print("== 轴 3 · ORM 模型使用率（静态启发式）==")
    models_dir = BACKEND / "app" / "db" / "models"
    if not models_dir.is_dir():
        _say("ERROR", "未找到 backend/app/db/models")
        raise SystemExit(2)

    classes: dict[str, str] = {}
    for f in sorted(models_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        for m in re.finditer(r"^class\s+(\w+)\s*\(", f.read_text(encoding="utf-8"), re.M):
            classes[m.group(1)] = f.name
    _say("INFO", f"模型类 {len(classes)} 个（分布在 {len({v for v in classes.values()})} 个文件）")

    scan_files = [
        p for p in (BACKEND / "app").rglob("*.py")
        if models_dir not in p.parents and p.name != "__init__.py"
    ]
    usages: dict[str, list[str]] = {c: [] for c in classes}
    for p in scan_files:
        text = p.read_text(encoding="utf-8", errors="replace")
        for c in classes:
            if re.search(rf"\b{re.escape(c)}\b", text):
                usages[c].append(str(p.relative_to(REPO)))

    zero = {c: f for c, f in classes.items() if not usages[c]}
    low = {c: usages[c] for c, f in classes.items() if 0 < len(usages[c]) <= 2}

    if zero:
        _crit(f"0 引用的模型（疑似建了未用，{len(zero)} 个）—— 静态启发式，需人工确认：")
        for c, f in sorted(zero.items()):
            _say("  ", f"{c}（定义于 models/{f}）")
    else:
        _info("无 0 引用模型 ✓")
    if low:
        _info(f"低引用（≤2 处）模型 {len(low)} 个，供人工判：")
        for c, files in sorted(low.items()):
            _say("  ", f"{c} → {', '.join(sorted(files))}")


# ─────────────────────── 轴 4：导出-引用可达性 ───────────────────────
#
# 为什么需要（闭审计报告 §6.3 自列的未覆盖项）：
#   `client/utils/**` 是能力层，**导出却零调用**只有一个含义——能力写了但没接线。
#   图搜就是先例：`search_api.uts:252 export function searchByImage` 全仓无调用方，
#   于是"多模态搜索"对用户完全触达不到，而任何文档都写着它"已实现"（台账 §7.3）。
#   这类缺口**静态可查、零成本**，故门禁化。

EXPORT_RE = re.compile(
    r"^\s*export\s+(?:async\s+)?(?:function|const|let|class)\s+([A-Za-z_$][\w$]*)",
    re.M,
)

# 允许"导出但暂无外部调用方"的白名单（每条须给理由，避免门禁被随手放宽）
EXPORT_ALLOWLIST: dict[str, str] = {
    # 供测试/脚本调用，或按契约预留的公开入口
    "PATH_AUTH_DEVICE": "契约常量（contract.uts），按契约预留",
}


def audit_exports() -> None:
    print("== 轴 4 · 导出-引用可达性（client/utils 零调用导出）==")
    utils_dir = CLIENT / "utils"
    if not utils_dir.is_dir():
        _say("ERROR", "未找到 client/utils")
        raise SystemExit(2)

    sources = _client_sources()
    decls: dict[str, list[str]] = {}   # 名字 → 声明位置
    for f in sorted(utils_dir.glob("*.uts")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in EXPORT_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            decls.setdefault(m.group(1), []).append(f"{f.relative_to(REPO)}:{line}")

    # 统计全仓引用（含本文件内部引用；排除声明行本身）
    zero_cap: list[str] = []   # 零调用的**能力函数**（真信号：写了没接线）
    zero_const: list[str] = [] # 零调用的**契约常量**（PATH_*/FIELD_*；属预留或端点未接线，需人工判）
    low: list[str] = []
    for name, places in sorted(decls.items()):
        if name in EXPORT_ALLOWLIST:
            continue
        refs = 0
        for f in sources:
            text = f.read_text(encoding="utf-8", errors="replace")
            for m in re.finditer(rf"\b{re.escape(name)}\b", text):
                line = text[: m.start()].count("\n") + 1
                if f"{f.relative_to(REPO)}:{line}" in places:
                    continue          # 声明行自身不算引用
                refs += 1
        is_const = name.startswith("PATH_") or name.startswith("FIELD_") or "contract.uts" in places[0]
        if refs == 0:
            (zero_const if is_const else zero_cap).append(f"{name}（{places[0]}）")
        elif refs <= 2:
            low.append(f"{name}（{places[0]}）→ {refs} 处引用")

    _say("INFO", f"client/utils 导出符号 {len(decls)} 个（白名单豁免 {len(EXPORT_ALLOWLIST)} 个）")
    # 能力函数零调用＝"能力写了但没接线"，正是图搜那类缺口的形态（台账 §7.3）→ CRITICAL
    if zero_cap:
        _crit(f"零调用的**能力导出**（写了没接线，{len(zero_cap)} 个）—— 需确认是「预留」还是「漏接线」：")
        for e in zero_cap:
            _say("  ", e)
    else:
        _info("无零调用的能力导出 ✓")
    # 契约常量零调用＝多为契约预留，或对应后端端点尚未接线 → 只作提示（避免噪音淹没真信号）
    if zero_const:
        _info(f"零调用的契约常量 {len(zero_const)} 个（PATH_*/FIELD_*，多为预留或端点未接线，供人工判）：")
        for e in zero_const:
            _say("  ", e)
    if low:
        _info(f"低引用（≤2 处）导出 {len(low)} 个，供人工判：")
        for e in low:
            _say("  ", e)


# ─────────────────────────── 入口 ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="harness 三轴审计（契约/客户端/模型）")
    ap.add_argument("axis", choices=["openapi", "client", "models", "exports", "all"])
    ap.add_argument("--skip-openapi", action="store_true", help="无后端环境时跳过契约轴")
    ap.add_argument("--json", metavar="PATH", help="把发现写入 JSON（供 CI/台账引用）")
    args = ap.parse_args()

    if args.axis in {"openapi", "all"} and not args.skip_openapi:
        audit_openapi()
    if args.axis in {"client", "all"}:
        audit_client()
    if args.axis in {"models", "all"}:
        audit_models()
    if args.axis in {"exports", "all"}:
        audit_exports()

    print("\n" + "-" * 62)
    if CRITICAL:
        print(f"存在 CRITICAL {len(CRITICAL)} 项 · INFO {len(INFO)} 项")
    else:
        print(f"无 CRITICAL · INFO {len(INFO)} 项")
    print("-" * 62)

    if args.json:
        Path(args.json).write_text(
            json.dumps({"critical": CRITICAL, "info": INFO}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print(f"已写入 {args.json}")

    return 1 if CRITICAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
