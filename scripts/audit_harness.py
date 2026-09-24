#!/usr/bin/env python3
"""harness 多轴审计（契约漂移 / 客户端漂移 / 模型使用率 / 导出可达性 / 文件体积 / 重复模式）

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
  4. **导出可达性**：`client/utils` 导出却零调用＝"能力写了没接线"（图搜先例）。
  5. **文件体积棘轮**（2026-09-24 重构波 B1）：巨型文件是改动风险放大器；
     存量超阈进基线白名单，**新增超阈即 CRITICAL**（防新债，不阻断存量）。
  6. **重复模式棘轮**（B1）：归属/软删过滤手抄（`deleted_at.is_(None)`）总数不得上升，
     推动走公共 helper（详见 docs 台账 P2-2 族）。

方法：
  · openapi：本地 `from app.main import app` 实时导出 paths，与 `docs/openapi.json` 做
    路径集 + 方法集**双向** diff。导入失败（缺依赖/环境）时明确报出原因而非 crash。
  · client：① 扫 `USE_MOCK_*` 赋值并清点真值 ② 从 `client/pages.json` 取已注册页面集，
    再扫 `client/**` 的 `/pages/x/y` 字面量——**按是否落在注释里分流**：
    注释内＝历史标注（INFO），代码内＝**死路由（CRITICAL）**
    ②b 反向对拍（D12-9）：`pages.json` 声明的页面必须在 `client/` 有对应物理文件
    （缺文件＝CRITICAL；此前只做"代码→声明"单向，声明了却没落文件完全无覆盖）
    ③ 扫 `.ts` 后缀引用（5.24 迁移后应为 `.uts`）。
  · models：正则取 `class X(` 类名，统计其在 `backend/app/**`（排除 models 定义处）
    的出现次数；0 引用＝疑似未使用（**静态启发式，非结论**，ORM 字符串式引用需人工判）。

用法：
  python scripts/audit_harness.py openapi|client|models|exports|filesize|dups|all
  python scripts/audit_harness.py all --json out.json      # 供 CI/台账引用

退出码：
  0 = 全部无 CRITICAL 发现
  1 = 存在 CRITICAL（契约双向漂移有差 / 代码内死路由 / pages.json 声明缺物理文件 /
      模型 0 引用 / 零调用能力导出 / 新增超阈文件 / 重复模式总数上升）
  2 = 执行环境错误（后端不可导入且非 --skip-openapi 等）

基线（棘轮）：`scripts/audit_harness_baseline.json`——存量超阈文件与重复模式计数在此冻结，
只允许**收缩**；新增违规直接 CRITICAL。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# 输出编码兜底：Windows 默认 GBK，`✓` 等符号会抛 UnicodeEncodeError 直接崩掉审计；
# 且 CI/本仓工具链按 UTF-8 解码管道输出。故统一按 UTF-8 输出，不可编码字符降级为 ?，
# 保证本脚本在任何终端/管道下都不会因编码问题失败。
# D14-11（B10-n）：UTF-8 兜底唯一实现（scripts/gate_io.py）
from gate_io import force_utf8  # noqa: E402

# D14-22（B10-i）：机读报告**统一 schema + 带兜底写入**的单一实现（与 review_agent/test_agent 共用）
from gate_report import write_report  # noqa: E402

force_utf8()
CLIENT = REPO / "client"
BACKEND = REPO / "backend"
DOCS_OPENAPI = REPO / "docs" / "openapi.json"
BASELINE_PATH = REPO / "scripts" / "audit_harness_baseline.json"

CRITICAL: list[str] = []
INFO: list[str] = []


def _load_baseline() -> dict:
    """棘轮基线（存量冻结）；缺失时返回空基线（此时任何超阈/重复都算新增）。"""
    if not BASELINE_PATH.exists():
        _say("WARN", f"未找到基线 {BASELINE_PATH.relative_to(REPO)} → 视基线为空")
        return {}
    return json.loads(BASELINE_PATH.read_text(encoding="utf-8"))


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

# 客户端**排除目录**（B10-o · D14-13 收敛）：此前这套集合在本文件内**硬编码 2 次**
# （`_client_sources` 与 `_iter_sized_sources`）⇒ 加一个排除目录要改两处、必漏一处。
CLIENT_EXCLUDE_DIRS = {"unpackage", "node_modules", ".hbuilderx"}


def _client_sources() -> list[Path]:
    out: list[Path] = []
    for p in CLIENT.rglob("*"):
        if not p.is_file() or p.suffix not in CODE_SUFFIXES:
            continue
        if any(part in CLIENT_EXCLUDE_DIRS for part in p.parts):
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


# 页面物理文件后缀：uni-app x 主用 `.uvue`（`.vue`/`.nvue` 兜底），避免后缀误判成缺文件。
PAGE_FILE_SUFFIXES = (".uvue", ".vue", ".nvue")


def _declared_pages_missing_files() -> list[str]:
    """`pages.json` 声明的页面 → 缺物理文件的清单（轴 2**反向**一侧，D12-9）。

    正向（② 已有）：代码引用 `/pages/x/y` 必须已在 `pages.json` 注册；
    反向（本函数）：`pages.json` 声明 `/pages/x/y` 必须在 `client/` 存在 `x/y.uvue`。
    缺任一向都会漏：只做正向时，「改路径忘建文件 / 删文件忘改声明」零覆盖，
    运行期表现为白屏或 `page not found`。
    """
    missing: list[str] = []
    for page in sorted(_registered_pages()):
        base = CLIENT / page
        if any(base.with_suffix(sfx).is_file() for sfx in PAGE_FILE_SUFFIXES):
            continue
        missing.append(page)
    return missing


_STRING_QUOTES = {"'", '"', "`"}


def _comment_ranges(text: str) -> list[tuple[int, int]]:
    """返回文本中所有**注释区间** `[start, end)`（行注释 `//`、块注释 `/* */`、HTML `<!-- -->`）。

    为什么需要（D14-14 / D14-15 · 2026-09-24 B10-j）：原实现用「本行前缀里有没有 `//`」
    来判注释，属**行级近似**，产生两类假阳性/漏判：
      · **块注释** `/* ... */` 内（该行没有 `//`）的页面引用被当成死路由 → 误报 CRITICAL；
      · 多行 `<!-- ... -->`（HTML 注释）同样漏判；
      · `.ts` 残留扫描**完全不看注释** ⇒ 注释里写「原 `'./auth.ts'`」（合法历史标注）也报 CRITICAL。
    本函数做**字符级**扫描，并**跳过字符串字面量**（否则 `"http://x"` 里的 `//` 会被误当注释），
    供上面两处按**偏移量**精确判定。
    """
    ranges: list[tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        two = text[i : i + 2]
        if ch in _STRING_QUOTES:
            quote = ch
            i += 1
            while i < n:
                if text[i] == "\\":
                    i += 2
                    continue
                if text[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if two == "//":
            j = text.find("\n", i)
            j = n if j == -1 else j
            ranges.append((i, j))
            i = j
            continue
        if two == "/*":
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            ranges.append((i, j))
            i = j
            continue
        if text[i : i + 4] == "<!--":
            j = text.find("-->", i + 4)
            j = n if j == -1 else j + 3
            ranges.append((i, j))
            i = j
            continue
        i += 1
    return ranges


def _in_comment(ranges: list[tuple[int, int]], offset: int) -> bool:
    """该**字符偏移**是否落在任一注释区间内（精确判定，替代"本行有 //"的近似）"""
    return any(s <= offset < e for s, e in ranges)


def audit_client() -> None:
    """轴 2：客户端漂移（mock 开关 / 页面路径双向对拍 / .ts 残留）。

    **扫描面判定（D12-9）：不纳入仓根 `uvue_gen/`。** 该目录是**生成物**
    （`*_gen.uvue` 画布原型 + `*_preview.html` + `*_selfreview.png` + `*_canvas.json`，
    共 232 文件），非编译进 app 的源码（app 源码根 = `client/`）。纳入会同时引入
    ① 假阳性（原型里的 `index/press/record` 等命名并非注册路由）与
    ② 假阴性（重新生成即可"漂"过门禁）；且生成物随时可被设计窗重刷，作为漂移基线不稳定。
    故判定**不宜纳入**本轴。若后续确需覆盖生成物，应另立"生成物清单 + 白名单阈值"轴，
    而非并入客户端源码漂移轴。同理，`unpackage`/`node_modules`/`.hbuilderx` 已在 `_client_sources` 排除。
    """
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

    # ② 页面路径漂移（按是否在**注释区间**分流；B10-j：改用字符级精确判定）
    pages = _registered_pages()
    page_re = re.compile(r"/pages/[A-Za-z0-9_]+/[A-Za-z0-9_-]+")
    dead_code: list[str] = []
    comment_refs: list[str] = []
    for f in sources:
        if f.name == "pages.json":
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        ranges = _comment_ranges(text)
        for m in page_re.finditer(text):
            target = m.group(0)
            if target.lstrip("/") in pages:  # 两侧均归一化后比较（见 _registered_pages 注）
                continue
            line = text[: m.start()].count("\n") + 1
            in_comment = _in_comment(ranges, m.start())
            # 「原」标注＝已声明的历史沿革（按本轴自身建议），不再计入漂移。
            # 取**整行**文本判「原」（块注释内"原"未必在匹配点之前）。
            line_start = text.rfind("\n", 0, m.start()) + 1
            line_end = text.find("\n", m.start())
            line_text = text[line_start : len(text) if line_end == -1 else line_end]
            if in_comment and "原" in line_text:
                continue
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

    # ②b 反向对拍（D12-9）：pages.json 声明的页面必须有物理文件
    #     与 ② 配对成双向——② 抓"代码引用未注册"，本步抓"声明了却没落文件"。
    missing_files = _declared_pages_missing_files()
    if missing_files:
        _crit(f"pages.json 声明但无物理文件（{len(missing_files)} 条）→ 运行时白屏/找不到页面：")
        for p in missing_files:
            _say("  ", f"{p}（缺 {p}.uvue）")
    else:
        _info(f"pages.json {len(pages)} 条声明均有物理文件 ✓")

    # ③ .ts 残留引用（B10-j：跳过**注释区间**——注释里写"原 './auth.ts'"属合法历史标注）
    ts_re = re.compile(r"from\s+['\"][^'\"]+\.ts['\"]|['\"][^'\"]+\.ts['\"]\s*(?:,|\)|\})")
    ts_hits: list[str] = []
    ts_in_comment: list[str] = []
    for f in sources:
        if f.suffix not in {".uts", ".uvue", ".ts", ".json"}:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        ranges = _comment_ranges(text)
        for m in ts_re.finditer(text):
            line = text[: m.start()].count("\n") + 1
            entry = f"{f.relative_to(REPO)}:{line}  {m.group(0).strip()}"
            (ts_in_comment if _in_comment(ranges, m.start()) else ts_hits).append(entry)
    if ts_hits:
        _crit(f"残留 .ts 引用（5.24 迁移后应为 .uts，{len(ts_hits)} 条）：")
        for e in sorted(set(ts_hits)):
            _say("  ", e)
    else:
        _info("无 .ts 残留引用 ✓（代码内；注释内的历史标注已单独计）")
    if ts_in_comment:
        _info(f"注释内 .ts 提及 {len(ts_in_comment)} 条（历史标注，不计漂移）：")
        for e in sorted(set(ts_in_comment)):
            _say("  ", e)


# ─────────────────────────── 轴 3：模型使用率 ───────────────────────────

# 类定义块（含 body）：从 `class X(` 到下一个 class 或文件末尾——用于**逐类**判定，
# 不再按整文件一刀切（D14-5）。
_CLASS_BLOCK_RE = re.compile(r"^class\s+(\w+)\s*\((?P<body>.*?)(?=^class\s|\Z)", re.M | re.S)
_TABLENAME_RE = re.compile(r"__tablename__\s*=")


def _module_docstring(text: str) -> str:
    """取模块级 docstring（文件起始处的三引号块）；无则返回空串。"""
    m = re.match(r'\s*(?P<q>"""|\'\'\')(?P<doc>.*?)(?P=q)', text, re.S)
    return m.group("doc") if m else ""


def audit_models() -> None:
    print("== 轴 3 · ORM 模型使用率（静态启发式）==")
    models_dir = BACKEND / "app" / "db" / "models"
    if not models_dir.is_dir():
        _say("ERROR", "未找到 backend/app/db/models")
        raise SystemExit(2)

    classes: dict[str, str] = {}
    mirror_tables: set[str] = set()
    for f in sorted(models_dir.glob("*.py")):
        if f.name.startswith("_"):
            continue
        text = f.read_text(encoding="utf-8")
        # 「镜像表」判定——该模块是**刻意镜像**（外部系统的表结构；仅为让 Base.metadata 与库
        # 一致、防 alembic --autogenerate 误生成 DROP TABLE），由迁移与漂移守卫消费，
        # 不参与本仓业务读写 → 0 引用属**预期**，不得判为"建了未用"（2026-09-24 B1 修假阳性）。
        # ⚠️ D14-5 收窄（2026-09-24 B10）：旧判据「文件**任意位置**出现『镜像』二字 →
        #   该文件**全部类**豁免」过宽——① 触发过宽：类里一句无关的「镜像」描述即豁免整个
        #   文件；② 范围过宽：同文件的**非表类**（枚举/mixin/工具）也被连带豁免。
        #   现要求**两条同时成立**才豁免：模块 docstring 声明含「镜像」**且**该类自身是表
        #   （声明了 `__tablename__`）；不满足者照常进入 0 引用检查。
        is_mirror_module = "镜像" in _module_docstring(text)
        for m in _CLASS_BLOCK_RE.finditer(text):
            name = m.group(1)
            classes[name] = f.name
            if is_mirror_module and _TABLENAME_RE.search(m.group("body")):
                mirror_tables.add(name)
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

    zero = {c: f for c, f in classes.items() if not usages[c] and c not in mirror_tables}
    mirrored = {c: f for c, f in classes.items() if not usages[c] and c in mirror_tables}
    low = {c: usages[c] for c, f in classes.items() if 0 < len(usages[c]) <= 2}

    if zero:
        _crit(f"0 引用的模型（疑似建了未用，{len(zero)} 个）—— 静态启发式，需人工确认：")
        for c, f in sorted(zero.items()):
            _say("  ", f"{c}（定义于 models/{f}）")
    else:
        _info("无 0 引用模型 ✓")
    if mirrored:
        _info(f"0 引用的**镜像表**模型 {len(mirrored)} 个（刻意声明，非缺口）：")
        for c, f in sorted(mirrored.items()):
            _say("  ", f"{c}（models/{f}，镜像：仅为 metadata/alembic 一致 + 漂移守卫）")
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
    r"^[ \t]*export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function|const|let|class|type|interface|enum)\s+([A-Za-z_$][\w$]*)",
    re.M,
)
# ⚠️ 2026-09-24（B10-f，D14-16）：覆盖面补全两处
#   ① 声明形态：原只认 `function|const|let|class` ⇒ `export type/interface/enum/default` 全部漏扫
#      （实测补上后 +5 个导出、其中零调用 0 个 ⇒ 不影响结论，但消除盲区）；
#   ② 目录深度：`audit_exports` 原用 `utils_dir.glob("*.uts")`（**非递归**）⇒ `client/utils/agg/**`
#      等子目录整体不在扫描面（实测补递归后 +29 个导出，其中 1 个零调用 `WALK_SPEED_MS`，
#      与深审 D04-12 相互印证）。
# ⚠️ 2026-09-24（B10-d）：原写法为 `^\s*export ...`——`\s` **包含换行**，于是 `^` 会先在前导
#   空行处成立、`\s*` 吃掉落行，导致 `m.start()` 落在**空行**而非 `export` 行。后果：
#   声明行号被记成空行号 ⇒ 下方"声明行自身不算引用"的排除失配 ⇒ 每个导出都把自身计成 1 处引用
#   ⇒ 真·零调用导出被降级进 `low` 而**永不报 CRITICAL**（假阴性，恰好会藏住图搜那类缺口）。
#   实测影响面：298 个导出中 56 个声明行偏移 1 行；`zero_cap` 由「应为 4」被掩盖为 **0**。
#   修为 `[ \t]*`（只吃横向空白）⇒ 匹配点=行首 ⇒ 排除生效、行号正确。

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
    for f in sorted(utils_dir.rglob("*.uts")):
        text = f.read_text(encoding="utf-8", errors="replace")
        for m in EXPORT_RE.finditer(text):
            line = text[: m.start()].count("\n") + 1
            decls.setdefault(m.group(1), []).append(f"{f.relative_to(REPO)}:{line}")

    # 统计全仓引用（含本文件内部引用；排除声明行本身；**排除注释**）
    # ⚠️ 2026-09-24（B10-q）：B5b 拆分生成的模块头 doc 会列出「对外导出：…」，而原实现
    #   用裸 \bname\b 全文本计数 ⇒ **注释里提到名字也算引用** ⇒ 真·零调用导出被注释掩盖
    #   （与 B10-d 同族的假阴性；B5b 的 doc 头当场把它顶成"僵尸豁免"CRITICAL 才暴露）。
    #   实测排除注释后多露出 4 枚真死码：AGG_CHECK_ON_DEVICE / invalidateTimelineCache /
    #   isIgnoredEvent / parseErrorString。口径与轴 2（audit_client 的 _comment_ranges）一致。
    #   顺带把"按名重读每份源文件"改为**预读缓存**（原为 导出数 × 文件数 次 read_text）。
    sources_cache: list[tuple[object, str, list[tuple[int, int]]]] = []
    for f in sources:
        text = f.read_text(encoding="utf-8", errors="replace")
        sources_cache.append((f, text, _comment_ranges(text)))
    base = _load_baseline().get("exports", {})
    frozen = base.get("zero_cap_allowlist", {})   # 棘轮：存量零调用能力导出（只拦新增）

    zero_cap: list[tuple[str, str]] = []   # 零调用的**能力函数**（真信号：写了没接线）
    zero_const: list[tuple[str, str]] = [] # 零调用的**契约常量**（PATH_*/FIELD_*；属预留或端点未接线，需人工判）
    low: list[str] = []
    for name, places in sorted(decls.items()):
        if name in EXPORT_ALLOWLIST:
            continue
        refs = 0
        pat = re.compile(rf"\b{re.escape(name)}\b")
        for f, text, ranges in sources_cache:
            for m in pat.finditer(text):
                line = text[: m.start()].count("\n") + 1
                if f"{f.relative_to(REPO)}:{line}" in places:
                    continue          # 声明行自身不算引用
                if _in_comment(ranges, m.start()):
                    continue          # 注释内提及不算引用（B10-q）
                refs += 1
        is_const = name.startswith("PATH_") or name.startswith("FIELD_") or "contract.uts" in places[0]
        if refs == 0:
            (zero_const if is_const else zero_cap).append((name, places[0]))
        elif refs <= 2:
            low.append(f"{name}（{places[0]}）→ {refs} 处引用")

    _say("INFO", f"client/utils 导出符号 {len(decls)} 个（白名单豁免 {len(EXPORT_ALLOWLIST)} 个）")
    # 能力函数零调用＝"能力写了但没接线"，正是图搜那类缺口的形态（台账 §7.3）→ CRITICAL。
    # 棘轮（口径同轴 5/6）：存量违规在基线 `exports.zero_cap_allowlist` **冻结、不阻断**，
    #   **只拦新增**；基线条目若已不再违规 → 亦报 CRITICAL（禁止僵尸豁免，棘轮只许收缩）。
    frozen_names = set(frozen)
    live_names = {n for n, _ in zero_cap}
    new_zero = [(n, p) for n, p in zero_cap if n not in frozen_names]
    kept = [(n, p) for n, p in zero_cap if n in frozen_names]
    stale = sorted(frozen_names - live_names)

    if new_zero:
        _crit(f"**新增**零调用的能力导出（写了没接线，{len(new_zero)} 个）—— 需接线或删除导出：")
        for n, p in new_zero:
            _say("  ", f"{n}（{p}）")
    else:
        _info("无**新增**零调用的能力导出 ✓")
    if kept:
        _info(f"存量零调用能力导出 {len(kept)} 个（基线冻结，销项触发见 baseline）：")
        for n, p in kept:
            _say("  ", f"{n}（{p}）")
    if stale:
        _crit(
            f"基线 exports.zero_cap_allowlist 有 {len(stale)} 条已**不再违规**（僵尸豁免）"
            f"——请从基线删除：{stale}"
        )
    # 契约常量零调用＝多为契约预留，或对应后端端点尚未接线 → 只作提示（避免噪音淹没真信号）
    if zero_const:
        _info(f"零调用的契约常量 {len(zero_const)} 个（PATH_*/FIELD_*，多为预留或端点未接线，供人工判）：")
        for n, p in zero_const:
            _say("  ", f"{n}（{p}）")
    if low:
        _info(f"低引用（≤2 处）导出 {len(low)} 个，供人工判：")
        for e in low:
            _say("  ", e)


# ─────────────────── 轴 5：文件体积棘轮 ───────────────────
#
# 为什么需要：巨型文件是**改动风险放大器**（本仓 900~2200 行级文件已成常态）。
# 棘轮口径（刻意不做"一夜全阻断"——那会被绕过）：存量超阈文件在 baseline 冻结、
# **不阻断**；**新增超阈文件即 CRITICAL**（新债不得产生）；存量再增长则 WARN。

# 体积阈值：**唯一配置源**是 `audit_harness_baseline.json` 的 `filesize.thresholds`；
# 本字典仅为基线/字段缺失时的兜底默认。
# ⚠️ D14-2（2026-09-24 B10）：该基线字段此前是**死配置**——代码硬编码同值、从不读取，
#   改基线阈值不生效（看似可调、实则无效）。现由 `_filesize_thresholds` 真正读取。
_DEFAULT_FILESIZE_THRESHOLDS = {"backend_py": 600, "client": 800}


def _filesize_thresholds(section: dict | None = None) -> dict[str, int]:
    """体积阈值：优先取基线 `filesize.thresholds`，缺失项回退默认。"""
    if section is None:
        section = _load_baseline().get("filesize", {})
    conf = section.get("thresholds", {})
    out = dict(_DEFAULT_FILESIZE_THRESHOLDS)
    if isinstance(conf, dict):
        for key, val in conf.items():
            with contextlib.suppress(TypeError, ValueError):
                out[str(key)] = int(val)
    return out


def _iter_sized_sources() -> list[Path]:
    out = list((BACKEND / "app").rglob("*.py"))
    out += [
        p
        for p in CLIENT.rglob("*")
        if p.is_file()
        and p.suffix in {".uts", ".uvue"}
        and not any(part in CLIENT_EXCLUDE_DIRS for part in p.parts)
    ]
    return sorted(out)


def _threshold_for(rel: str, thresholds: dict[str, int]) -> int:
    if rel.startswith("client/"):
        return thresholds["client"]
    return thresholds["backend_py"]


def _filesize_findings() -> tuple[list[str], list[str], list[str]]:
    """(新增超阈或僵尸豁免=CRITICAL, 存量超阈=INFO, 存量增长或基线过高=WARN)"""
    section = _load_baseline().get("filesize", {})
    allow = section.get("allowlist", {})
    thresholds = _filesize_thresholds(section)
    crit: list[str] = []
    info: list[str] = []
    warn: list[str] = []
    seen: set[str] = set()
    for p in _iter_sized_sources():
        rel = p.relative_to(REPO).as_posix()
        lines = len(p.read_text(encoding="utf-8", errors="replace").splitlines())
        limit = _threshold_for(rel, thresholds)
        if lines <= limit:
            continue
        seen.add(rel)
        if rel in allow:
            recorded = allow[rel]
            recorded = int(recorded["lines"]) if isinstance(recorded, dict) else int(recorded)
            if lines > recorded:
                warn.append(f"{rel} 基线 {recorded} → {lines} 行（超阈且增长，应抽取而非加长）")
            else:
                info.append(f"{rel} {lines} 行（基线 {recorded}，超阈已冻结）")
                if lines < recorded:
                    warn.append(
                        f"{rel} 已从 {recorded} 降到 {lines} 行（仍超阈）"
                        "——请下调基线计数（棘轮只许收缩，缩了要记账）"
                    )
        else:
            crit.append(f"{rel} {lines} 行 > 阈值 {limit}（新增超阈：拆分或显式申请基线）")
    # 僵尸豁免清算（D14-4 的一半 · 2026-09-24 B10-e）：基线条目若**已不再超阈**（含文件被删/改名/
    # 已拆到阈值内），该豁免就成为"永久白条"——若不报错，基线会慢慢烂成万能豁免池，棘轮名存实亡。
    for rel in sorted(set(allow) - seen):
        crit.append(
            f"基线 filesize.allowlist 的 {rel} 已不再超阈（文件被删/改名/已拆分到阈值内）"
            "——请从基线删除该条"
        )
    return crit, info, warn


def audit_filesize() -> None:
    t = _filesize_thresholds()
    print(f"== 轴 5 · 文件体积棘轮（backend/app *.py ≤{t['backend_py']} / client *.uts|*.uvue ≤{t['client']}）==")
    crit, info, warn = _filesize_findings()
    for m in crit:
        _crit(m)
    for m in warn:
        _say("WARN", m)
    if not crit:
        _info(f"无新增超阈文件 ✓（存量超阈 {len(info)} 个已冻结）")
    for m in info:
        _say("  ", m)


# ─────────────────── 轴 6：重复模式棘轮（归属/软删手抄） ───────────────────
#
# 为什么需要：`deleted_at.is_(None)` 手抄散落 20+ 文件＝同一语义抄 N 遍，新端点极易写漏
# （P2-2 族根因）。棘轮口径：**总数不得上升**（推动走公共 helper），上升即 CRITICAL。

DUP_PATTERNS = {"soft_delete_filter": r"deleted_at\.is_\(None\)"}


def _dup_findings() -> tuple[list[str], list[str]]:
    """(总数上升=CRITICAL, 现状=INFO)"""
    base = _load_baseline().get("dup_patterns", {})
    crit: list[str] = []
    info: list[str] = []
    for name, pattern in DUP_PATTERNS.items():
        entry = base.get(name, {})
        base_total = entry.get("total")
        base_files: dict[str, int] = entry.get("per_file", {})
        total = 0
        hits: dict[str, int] = {}
        growth: list[str] = []
        for p in (BACKEND / "app").rglob("*.py"):
            rel = p.relative_to(REPO).as_posix()
            n = len(re.findall(pattern, p.read_text(encoding="utf-8", errors="replace")))
            if not n:
                continue
            hits[rel] = n
            total += n
            if n > int(base_files.get(rel, 0)):
                growth.append(f"{rel} {base_files.get(rel, 0)} → {n} 处")
        # 棘轮口径（D14-3 修正，2026-09-24 B10）：**总数**与**单文件**两条腿都要 gate。
        # 旧版只 gate 总数 → 若「A 文件 +1 抵消 B 文件 -1」使总数不变，则新增的手抄点被
        # 静默放行——per_file 白名单（base_files）形同虚设，与体积轴「新增即拦」不一致。
        if base_total is None:
            info.append(f"{name}: 基线缺失（当前 {total} 处）——请补基线")
            continue
        if total > int(base_total) or growth:
            detail: list[str] = []
            if total > int(base_total):
                detail.append(f"总数 {base_total} → {total}")
            if growth:
                detail.append(f"单文件上升：{'; '.join(growth)}")
            crit.append(f"{name} 手抄上升（{'；'.join(detail)}）——推动走公共 helper")
        elif total < int(base_total):
            info.append(
                f"{name}: {total} 处（基线 {base_total}，**已下降**）"
                "——请同步下调基线 total 与 per_file（棘轮只许收缩，缩了要记账）"
            )
        else:
            info.append(f"{name}: {total} 处（基线 {base_total}，未上升 ✓）")
        # 僵尸豁免清算（B10-e，同轴 5 口径）：基线 per_file 中已**归零**的条目（文件被删/改名/
        # 手抄已收敛掉）必须删除，否则是永久白条 —— 棘轮只许收缩。
        for rel, recorded in sorted(base_files.items()):
            if int(recorded) > 0 and rel not in hits:
                crit.append(
                    f"基线 dup_patterns.{name}.per_file 的 {rel} 已不再命中"
                    f"（清零或文件被删/改名，原记 {recorded} 处）——请从基线删除该条"
                )
    return crit, info


def audit_dups() -> None:
    print("== 轴 6 · 重复模式棘轮（归属/软删手抄 `deleted_at.is_(None)`）==")
    crit, info = _dup_findings()
    for m in crit:
        _crit(m)
    if not crit:
        _info("重复模式总数未上升 ✓")
    for m in info:
        _say("  ", m)


def structure_findings() -> list[str]:
    """公共入口：结构棘轮（体积 + 重复模式）的 CRITICAL 列表。

    供 `review_agent` 等门禁复用——纯计算、不打印、不导入后端 app，秒级可跑。
    """
    return _filesize_findings()[0] + _dup_findings()[0]


def audit_client_imports_axis() -> None:
    """轴 6：客户端模块导出符号「用了却没 import」（2026-09-24 · B5.2f 实测暴露）。

    背景：HBuilderX CLI 的「项目 client 编译成功」**只覆盖语法/解析**，不覆盖符号解析——
    `new RecordAnimations()` 写在 .uvue 里却完全没有 import，编译**仍报成功**（对比：import 丢逗号
    这类语法错误会被 `[vue/compiler-sfc] Unexpected token` 抓到）。⇒ 必须有本轴静态补位。
    """
    from audit_client_imports import scan as _scan_client_imports

    violations, n = _scan_client_imports()
    if violations:
        for v in violations:
            CRITICAL.append(
                f"[客户端导入] {v['file']} 使用了 {v['symbol']}"
                f"（定义于 {', '.join(v['declared_in'])}）却缺少 import"
            )
    else:
        INFO.append(f"[客户端导入] 客户端模块导出符号使用均有 import（扫描导出 {n} 个）")


def audit_class_members_axis() -> None:
    """轴 7：客户端 UTS 类成员「用了却没声明」（2026-09-25 · GAP-2 实测暴露）。

    背景（实测）：B5.2f-2e 把 `saving` 迁入 `useVoiceRecord.uts` 时漏声明字段，方法体却写
    `this.saving.value` —— 编译报绿（轴 6 只管跨模块 import，管不到类成员），真机必崩。
    ⇒ 跨模块符号（轴 6）与类成员（本轴）是两道独立假绿面，须各自设防。
    """
    from audit_class_members import scan as _scan_class_members

    violations, checked, skipped = _scan_class_members()
    if violations:
        for v in violations:
            CRITICAL.append(
                f"[类成员] {v['file']} class {v['class']} 的 {v['kind']}{v['member']} 未声明"
                "（编译不覆盖符号解析，真机 undefined 崩溃）"
            )
    else:
        INFO.append(f"[类成员] 客户端 UTS 类成员解析一致（检查 {checked} 类，{skipped} 个 extends 类跳过）")


def audit_token_literals_axis() -> None:
    """轴 8：客户端硬编码色字面量棘轮（令牌波 P4 · 2026-09-25）。

    背景：令牌波开工实测——样式里 hex 638 次 / `rgb()/rgba()` 171 次 / 渐变 10 种（**代码口径**，已剔注释），
    而 `design_tokens.dtcg.json` 在样式里被引用 **0 次**。令牌化（P3）是逐域长活；没有尺子就会
    「一边收敛一边长新字面量」。棘轮口径同轴 5/6：三类总数任一上升、或任一文件任一计数上升 ⇒ CRITICAL；
    下降 ⇒ WARN（要求同步下调基线）；基线 per_file 归零 ⇒ CRITICAL（僵尸豁免清算）。
    """
    from audit_token_literals import findings as _findings

    crit, warn, info = _findings()
    for m in crit:
        CRITICAL.append(m)
    for m in warn:
        _say("WARN", m)
    for m in info:
        INFO.append(m)


def axes_findings() -> tuple[list[str], list[str], str]:
    """公共入口：**轴 1-4**（契约 / 客户端 / 模型 / 导出）的 (CRITICAL, INFO, 备注)。

    供 `review_agent` 等门禁复用（重构波 B10：此前门禁只覆盖轴 5/6，轴 1-4 仅能人工跑、
    CI 零命中——缺陷 D14-1）。实现要点：
      · 隔离模块全局 CRITICAL/INFO，调用各轴函数后原样还原，不污染 `main()` 的汇总；
      · 屏蔽各轴 stdout（门禁侧只打印汇总，避免刷屏），由其返回值承载结论；
      · **轴 1 需可导入的后端环境**（`from app.main import app`）：不可导入时降级为**跳过**
        （不阻断，理由写入备注），与审计脚本「退出码 2＝环境错误（非发现）」的语义一致——
        门禁只对**真发现**（CRITICAL）阻断，避免缺依赖环境把门禁变成假红灯。
    """
    global CRITICAL, INFO
    saved_crit, saved_info = CRITICAL, INFO
    CRITICAL, INFO = [], []
    notes: list[str] = []
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            try:
                audit_openapi()
            except SystemExit:
                notes.append("轴1 契约对拍跳过：后端 app 不可导入（缺依赖/环境）")
            audit_client()
            audit_models()
            audit_exports()
        crit, info = list(CRITICAL), list(INFO)
    finally:
        CRITICAL, INFO = saved_crit, saved_info
    return crit, info, "；".join(notes)


# ─────────────────────────── 入口 ───────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="harness 多轴审计（契约/客户端/模型/导出/体积/重复）")
    ap.add_argument(
        "axis",
        choices=[
            "openapi", "client", "client_imports", "class_members", "token_literals",
            "models", "exports", "filesize", "dups", "all",
        ],
    )
    ap.add_argument("--skip-openapi", action="store_true", help="无后端环境时跳过契约轴")
    ap.add_argument("--json", metavar="PATH", help="把发现写入 JSON（供 CI/台账引用）")
    args = ap.parse_args()

    if args.axis in {"openapi", "all"} and not args.skip_openapi:
        audit_openapi()
    if args.axis in {"client", "all"}:
        audit_client()
    if args.axis in {"client_imports", "all"}:
        audit_client_imports_axis()
    if args.axis in {"class_members", "all"}:
        audit_class_members_axis()
    if args.axis in {"token_literals", "all"}:
        audit_token_literals_axis()
    if args.axis in {"models", "all"}:
        audit_models()
    if args.axis in {"exports", "all"}:
        audit_exports()
    if args.axis in {"filesize", "all"}:
        audit_filesize()
    if args.axis in {"dups", "all"}:
        audit_dups()

    print("\n" + "-" * 62)
    if CRITICAL:
        print(f"存在 CRITICAL {len(CRITICAL)} 项 · INFO {len(INFO)} 项")
    else:
        print(f"无 CRITICAL · INFO {len(INFO)} 项")
    print("-" * 62)

    if args.json:
        # D14-22（B10-i）：原写 `{critical, info}`——与 review-report.json **键集无交集**，
        #   且 write_text 无兜底（路径不可写即崩）。改为统一信封（schema_version/generated_at/passed）
        #   + 带兜底写入；自有键（critical/info）保留。
        if write_report(args.json, {
            "tool": "audit_harness",
            "passed": not CRITICAL,
            "critical": CRITICAL,
            "info": INFO,
            "critical_count": len(CRITICAL),
        }):
            print(f"已写入 {args.json}")

    return 1 if CRITICAL else 0


if __name__ == "__main__":
    raise SystemExit(main())
