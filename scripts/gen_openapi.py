#!/usr/bin/env python3
"""再生 / 校验 `docs/openapi.json`（契约快照）。

为什么需要（D14-7 · 2026-09-24 重构波 B10-g）：
  契约快照此前**只能人工手改**——`scripts/audit_harness.py` 轴 1 只会告诉你
  「后端有路由但契约缺」，却**没有再生工具**；于是每次都要手工编辑 ~9 千行 JSON
  （极易漏字段/写错），也让 CI 无从断言「快照是否已过期」。本脚本把该人工步骤
  变成可复现命令，并提供 `--check` 供门禁/CI 断言。

用法（仓库根）：
    python scripts/gen_openapi.py            # 写入 docs/openapi.json
    python scripts/gen_openapi.py --check    # 只校验：与磁盘一致=0，不一致=1

退出码：
    0 = 写入成功 / --check 一致
    1 = --check 发现快照过期（需重导）
    2 = 执行环境错误（后端不可导入：缺依赖/环境变量）
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BACKEND = REPO / "backend"
TARGET = REPO / "docs" / "openapi.json"


def render() -> str:
    """实时导出后端 openapi 快照文本（UTF-8 非转义 + 2 空格缩进 + 末尾换行）"""
    sys.path.insert(0, str(BACKEND))
    from app.main import app  # type: ignore[import-not-found]

    return json.dumps(app.openapi(), ensure_ascii=False, indent=2) + "\n"


def _diff_hint(current: str, fresh: str) -> None:
    """打印定位提示（避免只甩一句「不一致」）"""
    try:
        cur_paths = set(json.loads(current).get("paths", {}))
        new_paths = set(json.loads(fresh).get("paths", {}))
    except Exception:  # noqa: BLE001 — 快照不可解析时仅报"不一致"
        print("  （快照无法解析为 JSON，无法给出路径级定位）")
        return
    missing = sorted(new_paths - cur_paths)
    extra = sorted(cur_paths - new_paths)
    if missing:
        print(f"  后端有而快照缺（{len(missing)}）: {missing[:10]}")
    if extra:
        print(f"  快照有而后端无（{len(extra)}）: {extra[:10]}")
    if not missing and not extra:
        print("  路径集合一致 ⇒ 差异在字段级（schema/参数/描述/状态码等）——仍需重导")


def main() -> int:
    ap = argparse.ArgumentParser(description="再生/校验 docs/openapi.json 契约快照")
    ap.add_argument("--check", action="store_true", help="只校验一致性（不一致 → 退出码 1）")
    args = ap.parse_args()

    try:
        fresh = render()
    except Exception as exc:  # noqa: BLE001 — 环境问题原样报出
        print(f"[ERROR] 无法导出 openapi（{type(exc).__name__}: {exc}）")
        print("  → 契约轴需要可导入的后端环境（依赖与环境变量齐备）")
        return 2

    current = TARGET.read_text(encoding="utf-8") if TARGET.exists() else ""
    rel = TARGET.relative_to(REPO).as_posix()

    if args.check:
        if current == fresh:
            print(f"[OK] {rel} 与后端实现一致（{len(fresh)} 字符）")
            return 0
        print(f"[FAIL] {rel} 已过期——请运行 `python scripts/gen_openapi.py` 重导")
        _diff_hint(current, fresh)
        return 1

    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(fresh, encoding="utf-8")
    print(f"[OK] 已写入 {rel}（{len(fresh)} 字符，{'有更新' if current != fresh else '内容未变'}）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
