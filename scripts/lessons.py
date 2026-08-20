#!/usr/bin/env python3
"""教训台账工具（程序化强制 · 2026-08-20）

设计（用户要求：开发阶段每次排查修复后，犯错踩坑必须写入 harness 文档，
且为程序化强制，非提示词级别）：

机制：
1. `python scripts/lessons.py add --error <错误描述> --root-cause <根因> [--fix <修复>] [--file <相关文件>]`
   → 追加一条结构化教训到 docs/lessons.md（含日期/commit/错误/根因/修复）
2. review_agent.py 集成：
   - 检查失败时 → 写 .cowork-temp/last-failure.json（记录失败项+时间戳）
   - 检查通过时 → 若 last-failure.json 存在（说明上次失败过），且 lessons.md
     在失败时间之后无新登记 → 阻断提交，提示先登记教训（`lessons.py add`）
   → 程序化闭环：失败 → 必须登记教训 → 才能提交

用法：
  python scripts/lessons.py add --error "..." --root-cause "..." [--fix "..." --file "path"]
  python scripts/lessons.py recent [--days 7]
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
LESSONS_PATH = ROOT / "docs" / "lessons.md"
STATE_PATH = ROOT / ".cowork-temp" / "last-failure.json"

HEADER = """# 教训台账（Harness 强制登记 · 2026-08-20 起）

> 规则（程序化强制，见 scripts/lessons.py + review_agent.py check_lessons）：
> 开发阶段每次排查错误并修复后，必须登记一条教训——review_agent 检查失败后
> 未登记新教训会阻断 commit。格式固定，勿手改结构。
>
> 新增：`python scripts/lessons.py add --error "..." --root-cause "..." [--fix "..." --file "..."]`

---

"""


def _git_head() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, cwd=str(ROOT), encoding="utf-8",
            check=False,
        )
        return out.stdout.strip() or "?"
    except Exception:  # noqa: BLE001
        return "?"


def add(args) -> int:
    """追加一条教训（文件不存在则建表头）"""
    now = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    now_epoch = int(datetime.now().astimezone().timestamp())
    commit = _git_head()
    entry = (
        f"### {now} · commit {commit} · ts={now_epoch}\n"
        f"- **错误**：{args.error}\n"
        f"- **根因**：{args.root_cause}\n"
        f"- **修复**：{args.fix or '见代码'}\n"
        f"- **相关文件**：{args.file or '-'}\n"
        f"- **教训**：{args.lesson or '（无）'}\n"
        f"\n---\n\n"
    )
    if LESSONS_PATH.exists():
        existing = LESSONS_PATH.read_text(encoding="utf-8")
        # 插到第一个 --- 之后（表头后）
        if existing.startswith(HEADER):
            existing = existing[len(HEADER):]
        else:
            # 迁移：旧文件无表头 → 重建
            existing = ""
    else:
        existing = ""
    LESSONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    LESSONS_PATH.write_text(HEADER + entry + existing, encoding="utf-8")
    print(f"✅ 教训已登记：{LESSONS_PATH}")
    print(entry.strip())
    return 0


def recent(args) -> int:
    """最近 N 天登记（默认 7）"""
    if not LESSONS_PATH.exists():
        print("lessons.md 不存在（尚无登记）")
        return 0
    s = LESSONS_PATH.read_text(encoding="utf-8")
    cutoff = datetime.now().astimezone() - timedelta(days=args.days)
    count = 0
    for line in s.splitlines():
        if line.startswith("### "):
            try:
                ts = datetime.strptime(line[4:20], "%Y-%m-%d %H:%M").replace(tzinfo=datetime.now().astimezone().tzinfo)
                if ts >= cutoff:
                    count += 1
                    print(line[4:])
            except ValueError:
                pass
    print(f"最近 {args.days} 天共 {count} 条登记")
    return 0


def _last_entry_time() -> datetime | None:
    """lessons.md 最新一条登记时间（优先 ts= epoch 字段，兼容旧格式）"""
    if not LESSONS_PATH.exists():
        return None
    s = LESSONS_PATH.read_text(encoding="utf-8")
    for line in s.splitlines():
        if line.startswith("### "):
            import re as _re
            m = _re.search(r"ts=(\d+)", line)
            if m:
                return datetime.fromtimestamp(int(m.group(1)), tz=datetime.now().astimezone().tzinfo)
            try:
                tz = datetime.now().astimezone().tzinfo
                return datetime.strptime(line[4:20], "%Y-%m-%d %H:%M").replace(tzinfo=tz)
            except ValueError:
                continue
    return None


def check_lessons() -> tuple[bool, str]:
    """review_agent 集成：上次检查失败后是否已登记新教训

    返回 (ok, message)。规则：
    - 无失败状态文件 → OK（首次/连续通过）
    - 有失败状态 + lessons.md 最新登记晚于失败时间 → OK
    - 有失败状态 + 无新登记 → 阻断（强制先登记教训）
    """
    if not STATE_PATH.exists():
        return True, "无历史失败记录"
    try:
        state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return True, "失败状态文件损坏（忽略）"
    failed_at = state.get("failed_at")
    failed_ts = state.get("ts")
    if not failed_at and not failed_ts:
        return True, "失败状态无时间戳（忽略）"
    if failed_ts is not None:
        failed_dt = datetime.fromtimestamp(int(failed_ts), tz=datetime.now().astimezone().tzinfo)
    else:
        try:
            tz = datetime.now().astimezone().tzinfo
            failed_dt = datetime.strptime(failed_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=tz)
        except ValueError:
            return True, "失败时间戳格式异常（忽略）"
    last = _last_entry_time()
    if last is not None and last > failed_dt:
        return True, f"教训已登记（{last:%Y-%m-%d %H:%M} 晚于失败 {failed_dt:%Y-%m-%d %H:%M}）"
    return (
        False,
        f"上次检查失败（{failed_dt:%Y-%m-%d %H:%M}）后未登记教训。"
        f"强制要求：先执行 `python scripts/lessons.py add --error \"<错误>\" --root-cause \"<根因>\"` "
        f"登记到 docs/lessons.md 才能提交。",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="教训台账工具")
    sub = parser.add_subparsers(dest="cmd")
    p_add = sub.add_parser("add", help="登记一条教训")
    p_add.add_argument("--error", required=True, help="错误描述")
    p_add.add_argument("--root-cause", required=True, help="根因")
    p_add.add_argument("--fix", default="", help="修复方式")
    p_add.add_argument("--file", default="", help="相关文件")
    p_add.add_argument("--lesson", default="", help="教训（一句话）")
    p_add.set_defaults(func=add)

    p_recent = sub.add_parser("recent", help="最近登记")
    p_recent.add_argument("--days", type=int, default=7)
    p_recent.set_defaults(func=recent)

    args = parser.parse_args()
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
