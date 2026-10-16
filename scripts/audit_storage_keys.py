"""客户端 storage 键「写入但无消费者」审计（2026-10-14 · 台账 §4.20 批次 7）。

## 为什么有这条规则

2026-10-14 的审计抓到一族**假开关**（台账 §7.18）：`privacy_ai_chat`／`privacy_echo`／
`settings_notify_on`／`settings_theme_index`／`settings_cache_cleared` —— 它们被
`uni.setStorageSync` 写入（UI 上是一个会记住状态的开关），但**全仓没有任何读取方**，
用户以为自己在控制隐私／通知／主题／缓存，实际什么都没发生（隐私两枚属**空承诺**，合规相关）。

这类缺陷**编译能过、类型能过、人工 review 也容易滑过**（写与读分散在不同文件），
唯一的机械判据就是：**「写了却从来没人读」**。本脚本即该判据的实现。

## 口径

  · 「写入」= `setStorageSync(key, …)`；「消费者」= `getStorageSync(key)` / `removeStorageSync(key)`；
  · 键名解析支持**字面量**与**标识符常量**（`const KEY_X = '…'`，含跨文件定义——本项目两种都用）；
  · 解析不出的实参（函数参数、动态拼接）**不参与判定**，但会在输出里列出（覆盖率透明）；
  · 已知「写入即终态、本就不该有读取方」的键须在 `ALLOW_WRITE_ONLY` 里**显式登记理由**——
    不允许用沉默豁免（那正是本规则要消灭的东西）。

## 用法

    python scripts/audit_storage_keys.py            # 人类可读
    python scripts/audit_storage_keys.py --json     # 机读（供门禁）
    python scripts/audit_storage_keys.py --selftest # 负向探针：逻辑自检（必须能抓到注入的坏样本）

退出码：0 = 无违规；1 = 存在「写入无消费者」的键；2 = 环境错误（client/ 不存在）。
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CLIENT = REPO / "client"
SKIP_PARTS = {"unpackage", "uni_modules"}

# 显式豁免：**写入即终态**的键（每条必须写明理由；沉默豁免一律不接受）
ALLOW_WRITE_ONLY: dict[str, str] = {
    # 目前为空——2026-10-14 清理后无此类键。若将来确需豁免，按下面格式写：
    # "yishu_xxx": "写入即终态：仅作诊断打点，设计上无读取方（依据：docs/决策台账.md §…）",
}

CALL_RE = re.compile(r"(set|get|remove)StorageSync\(\s*([^,)]+?)\s*[,)]")
LITERAL_RE = re.compile(r"^['\"]([^'\"]+)['\"]$")
CONST_RE = re.compile(
    r"(?:export\s+)?(?:const|let)\s+([A-Za-z_]\w*)\s*(?::\s*[A-Za-z_|\[\]\s]+)?=\s*['\"]([^'\"]+)['\"]"
)


def _client_files() -> list[Path]:
    if not CLIENT.exists():
        return []
    out: list[Path] = []
    for p in sorted(list(CLIENT.rglob("*.uts")) + list(CLIENT.rglob("*.uvue"))):
        if SKIP_PARTS & set(p.parts):
            continue
        out.append(p)
    return out


def _resolve(arg: str, const_map: dict[str, set[str]]) -> set[str]:
    """实参 → 可能的键名集合（解析不出返回空集）。多候选时全部计入（保守：宁可少报）。"""
    lit = LITERAL_RE.match(arg.strip())
    if lit:
        return {lit.group(1)}
    return set(const_map.get(arg.strip(), set()))


def scan() -> tuple[list[dict], dict]:
    """返回 (violations, stats)。

    violations: [{"key": str, "written_in": [相对路径], "note": str}]
    """
    files = _client_files()
    if not files:
        return [], {"files": 0}

    texts: dict[Path, str] = {}
    for p in files:
        texts[p] = p.read_text(encoding="utf-8", errors="replace")

    # 全局常量表：键名常量常定义在与调用点不同的文件（如 `export const CHAT_CONV_KEY = '…'`）
    const_map: dict[str, set[str]] = collections.defaultdict(set)
    for text in texts.values():
        for m in CONST_RE.finditer(text):
            const_map[m.group(1)].add(m.group(2))

    writes: dict[str, set[str]] = collections.defaultdict(set)
    reads: dict[str, set[str]] = collections.defaultdict(set)
    dynamic: list[str] = []
    dynamic_files: set[str] = set()

    for p, text in texts.items():
        try:
            rel = str(p.relative_to(REPO)).replace("\\", "/")
        except ValueError:  # 负向探针用临时目录（不在仓库内）⇒ 退化为绝对路径，不影响判定
            rel = str(p).replace("\\", "/")
        for m in CALL_RE.finditer(text):
            kind, raw = m.group(1), m.group(2).strip()
            keys = _resolve(raw, const_map)
            if not keys:
                dynamic.append(f"{rel}: {kind}StorageSync({raw})")
                dynamic_files.add(rel)
                continue
            for k in keys:
                {"set": writes, "get": reads, "remove": reads}[kind][k].add(rel)

    violations: list[dict] = []
    for key in sorted(writes):
        if key in reads or key in ALLOW_WRITE_ONLY:
            continue
        files_written = sorted(writes[key])
        note = ""
        # 写入文件里若还有"解析不出的" storage 调用，则可能是**读取方藏在动态调用里**
        # ⇒ 明确提示，避免误判（覆盖率透明，不静默）
        hits = [d for d in dynamic if d.split(":")[0] in files_written]
        if hits:
            note = "⚠️ 该文件另有解析不出的 storage 调用（可能是读取方）：" + "；".join(hits[:3])
        violations.append({"key": key, "written_in": files_written, "note": note})

    stats = {
        "files": len(files),
        "write_keys": len(writes),
        "read_keys": len(reads),
        "dynamic_sites": len(dynamic),
        "dynamic_files": len(dynamic_files),
        "allowed": len(ALLOW_WRITE_ONLY),
    }
    return violations, stats


def selftest(verbose: bool = True) -> int:
    """负向探针：**必须能抓到坏样本**，且**不误报**已接线的键。

    为什么必须有：门禁的价值全在"不满足条件时能判红"（本项目 2026-10-14 刚修过一次
    编译门假通过）。本探针不依赖仓库现状，用内联样本直接验证判定逻辑：
      ① 只写不读的键 → 必须被抓到；
      ② 写了也被读的键 → 必须不报；
      ③ 跨文件常量解析（写入用 A 文件常量、读取用 B 文件常量）→ 必须不报。
    """
    import tempfile

    global CLIENT  # noqa: PLW0603 —— 探针临时把扫描根指向样本目录
    original = CLIENT
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "a.uts").write_text(
            "export const SHARED_KEY = 'shared_between_files'\n"
            "function w(): void {\n"
            "  uni.setStorageSync('dead_switch_key', '1')\n"
            "  uni.setStorageSync(SHARED_KEY, 'x')\n"
            "}\n",
            encoding="utf-8",
        )
        (root / "b.uvue").write_text(
            "import { SHARED_KEY } from './a.uts'\n"
            "const k = uni.getStorageSync('alive_key')\n"
            "const s = uni.getStorageSync(SHARED_KEY)\n",
            encoding="utf-8",
        )
        CLIENT = root
        try:
            violations, _stats = scan()
        finally:
            CLIENT = original

    keys = {v["key"] for v in violations}
    failures: list[str] = []
    if "dead_switch_key" not in keys:
        failures.append("未抓到「只写不读」的坏样本 dead_switch_key")
    if "alive_key" in keys:
        failures.append("误报：alive_key 有读取方却被判违规")
    if "shared_between_files" in keys:
        failures.append("误报：跨文件常量共享键被误判（常量解析未跨文件）")

    if failures:
        if verbose:
            print("[selftest 失败] " + "；".join(failures))
        return 1
    if verbose:
        print("[selftest 通过] 坏样本被抓到、好样本未误报（含跨文件常量解析）")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="客户端 storage 键「写入无消费者」审计")
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
            print("storage 键「写入但无消费者」：")
            for v in violations:
                print(f"  - {v['key']}  写于 {', '.join(v['written_in'])}")
                if v["note"]:
                    print(f"      {v['note']}")
        else:
            print(
                f"storage 键审计通过（写 {stats['write_keys']} / 读 {stats['read_keys']} / "
                f"解析不出 {stats['dynamic_sites']} 处，已豁免 {stats['allowed']}）"
            )
    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
