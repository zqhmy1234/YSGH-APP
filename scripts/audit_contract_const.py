"""客户端「契约路径常量声明了却没人用」审计（2026-10-14 · 台账 §4.20 ⑪ 第 10 轴）。

## 为什么有这条规则

`client/utils/contract.uts` 是端点路径的**单一事实源**（对齐 `docs/openapi.json`）。审计实测
（台账 §4.20 ⑮ 的 RC-A 族"定义即交付错觉"）：9 个路径常量**声明后从未被引用**，而对应的调用点
各自写着**字面量**——典型如 `timeline.uts` 拉 `/api/v1/events/timeline` 用字面量、
`PATH_EVENTS_TIMELINE` 却挂在契约里没人用。

危害不是"常量没用到"本身，而是**两处会各自漂移**：后端改路径时改了契约常量、调用点的字面量
不动 ⇒ 请求打到不存在的路径；反过来调用点改了、契约没跟 ⇒ 契约文档失真（本项目 2026-10-14
刚在 §7.3 吃过一次"台账/契约与代码不一致"的亏）。这类缺陷**编译能过、类型能过、人工 review
也滑过**（声明与使用分散在不同文件），唯一机械判据就是：**声明了却从来没人引用**。

## 口径

  · 目标 = 客户端 `.uts`/`.uvue` 里**值为 `/…` 的 UPPER_SNAKE 常量**（路径契约常量）；
  · 「使用」= 全仓（**含定义文件自身**——同文件内调用是完全合法的用法）出现该标识符，
    **定义行本身不计**；
  · 允许 `ALLOW_UNUSED` 显式登记豁免（每条必须写明理由）——**沉默豁免不接受**（同
    `audit_storage_keys.py` 的口径：豁免要留痕）。
  · 已知边界：若某常量只在**注释**里被提及，本脚本仍算"使用"（注释提及说明有人知道它，
    但**可能仍是漏接线**）——这类由人工在写注释时判断，机械规则不猜。

## 用法

    python scripts/audit_contract_const.py            # 人类可读
    python scripts/audit_contract_const.py --json     # 机读（供门禁）
    python scripts/audit_contract_const.py --selftest # 负向探针：必须能抓到坏样本

退出码：0 = 无违规；1 = 存在"声明未使用"的路径常量；2 = 环境错误（client/ 不存在）。
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLIENT = REPO / "client"
SKIP_PARTS = {"unpackage", "uni_modules"}

# 显式豁免：**契约镜像但客户端确实无调用点**的常量（每条必须写理由）
# 2026-10-15（台账 §7.29 · unionid 对齐重构）：`PATH_AUTH_WECHAT` 的豁免已**销项** ——
# 客户端接上了微信授权登录（`utils/auth.uts::wechatLogin`：uni.login → POST /auth/wechat），
# 该常量不再"声明未使用"。⚠️ 原豁免理由（"微信登录是后端专用流程、客户端走设备码"）
# **本身是错的**：它把"客户端没接"当成了"不该接"，正是这次要修的产品缺口。
# 棘轮口径：销项后必须删除条目（否则门禁报"僵尸豁免"CRITICAL）。
ALLOW_UNUSED: dict[str, str] = {}

DECL_RE = re.compile(
    r"(?:export\s+)?const\s+([A-Z_][A-Z0-9_]*)\s*(?::\s*[A-Za-z_|\[\]\s]+)?=\s*(['\"])(/[^'\"]*)\2"
)
NAME_RE_CACHE: dict[str, re.Pattern] = {}


def _client_files() -> list[Path]:
    if not CLIENT.exists():
        return []
    out = list(CLIENT.rglob("*.uts")) + list(CLIENT.rglob("*.uvue"))
    return [p for p in out if not (SKIP_PARTS & set(p.parts))]


def _rel(p: Path) -> str:
    try:
        return str(p.relative_to(REPO)).replace("\\", "/")
    except ValueError:  # 负向探针用临时目录 ⇒ 退化为绝对路径，不影响判定
        return str(p).replace("\\", "/")


def scan() -> tuple[list[dict], dict]:
    """返回 (violations, stats)。

    violations: [{"name", "value", "defined_in", "note"}]
    """
    files = _client_files()
    if not files:
        return [], {"files": 0}

    texts = {p: p.read_text(encoding="utf-8", errors="replace") for p in files}

    decls: dict[str, tuple[str, str]] = {}
    for p, t in texts.items():
        for m in DECL_RE.finditer(t):
            decls.setdefault(m.group(1), (_rel(p), m.group(3)))

    violations: list[dict] = []
    checked = 0
    for name, (defined_in, value) in sorted(decls.items()):
        name_re = NAME_RE_CACHE.setdefault(name, re.compile(r"\b" + re.escape(name) + r"\b"))
        decl_line_re = re.compile(r"(?:export\s+)?const\s+" + re.escape(name) + r"\s*(?::[^=]+)?=")
        checked += 1
        used = False
        for t in texts.values():
            for line in t.splitlines():
                if name_re.search(line) and not decl_line_re.search(line):
                    used = True
                    break
            if used:
                break
        if used or name in ALLOW_UNUSED:
            continue
        violations.append(
            {
                "name": name,
                "value": value,
                "defined_in": defined_in,
                "note": "调用点可能仍在用字面量（本脚本只能判「声明未使用」，具体调用点需人工定位）",
            }
        )

    stats = {"files": len(files), "declared": checked, "allowed": len(ALLOW_UNUSED)}
    return violations, stats


def selftest(verbose: bool = True) -> int:
    """负向探针：**必须能抓到坏样本**，且**不误报**已使用的常量。

    为什么必须有（同 `audit_storage_keys.selftest`）：门禁的价值全在"不满足条件时能判红"。
    本探针不依赖仓库现状，用内联样本直接验证判定逻辑：
      ① 声明后无人引用的路径常量 → 必须被抓到；
      ② 同文件内被使用的常量 → 必须不报（防"只统计跨文件引用"的经典误判）；
      ③ 跨文件被使用的常量 → 必须不报；
      ④ 非路径值（如 'wgs84'）不参与判定 → 不报（规则只盯 `/…` 契约路径）。
    """
    import tempfile

    global CLIENT  # noqa: PLW0603 —— 探针临时把扫描根指向样本目录
    original = CLIENT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "contract.uts").write_text(
            "export const PATH_DEAD: string = '/api/v1/nobody/calls/this'\n"
            "export const PATH_LOCAL: string = '/api/v1/used/in/same/file'\n"
            "export const PATH_CROSS: string = '/api/v1/used/across/files'\n"
            "export const NOT_A_PATH: string = 'wgs84'\n",
            encoding="utf-8",
        )
        (root / "user.uts").write_text(
            "import { PATH_CROSS } from './contract.uts'\n"
            "function f(): void {\n"
            "  get(PATH_LOCAL)\n"
            "  get(PATH_CROSS)\n"
            "  get(NOT_A_PATH)\n"
            "}\n",
            encoding="utf-8",
        )
        CLIENT = root
        try:
            violations, _stats = scan()
        finally:
            CLIENT = original

    names = {v["name"] for v in violations}
    failures: list[str] = []
    if "PATH_DEAD" not in names:
        failures.append("未抓到「声明未使用」的坏样本 PATH_DEAD")
    if "PATH_LOCAL" in names:
        failures.append("误报：PATH_LOCAL 在定义文件内被使用却被判违规")
    if "PATH_CROSS" in names:
        failures.append("误报：PATH_CROSS 跨文件被使用却被判违规")
    if "NOT_A_PATH" in names:
        failures.append("误报：非路径值常量 NOT_A_PATH 不应参与判定")

    if failures:
        if verbose:
            print("[selftest 失败] " + "；".join(failures))
        return 1
    if verbose:
        print("[selftest 通过] 坏样本被抓到；同文件/跨文件使用与非路径值均未误报")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="客户端契约路径常量「声明未使用」审计")
    ap.add_argument("--json", action="store_true", help="输出 JSON（门禁用）")
    ap.add_argument("--selftest", action="store_true", help="只跑逻辑自检（负向探针）")
    args = ap.parse_args()

    if args.selftest:
        return selftest()

    if not CLIENT.exists():
        payload = {"status": "env_error", "detail": f"client/ 目录不存在：{CLIENT}"}
        print(json.dumps(payload, ensure_ascii=False) if args.json else payload["detail"])
        return 2

    violations, stats = scan()
    if args.json:
        print(json.dumps({"violations": violations, "stats": stats}, ensure_ascii=False))
    else:
        if violations:
            print("契约路径常量「声明了却没人用」：")
            for v in violations:
                print(f"  - {v['name']} = {v['value']}  定义于 {v['defined_in']}")
                if v["note"]:
                    print(f"      {v['note']}")
        else:
            print(
                f"契约常量审计通过（客户端扫描 {stats['files']} 文件 / 路径常量 "
                f"{stats['declared']} 个，豁免 {stats['allowed']}）"
            )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
