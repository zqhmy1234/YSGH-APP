"""HBuilderX 项目唯一性守卫。

背景：HBuilderX 5.24 CLI 的 `--project client` 只按名称选项目。2026-10-23
实测工作区同时登记了当前 `D:\\projects\\YSGH-APP\\client` 和旧
`D:\\projects\\YSGH-PR3\\client`，CLI 静默选中旧版，导致“编译成功”与真机
行为仍停留在 versionCode 102。

规则：任何编译/打包前，HBuilderX 注册表中名为 `client` 的项目必须恰好一个，
且路径必须等于当前仓库的 `client/`。旧项目按 APK versionCode 归档为
`client-<versionCode>`。
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_EXPECTED = REPO / "client"
_PROJECT_PATH_RE = re.compile(r"^projects\\(\d+)\\path=(.+)$")


def default_registry_path() -> Path:
    override = os.environ.get("HBUILDERX_WORKSPACE_INI")
    if override:
        return Path(override)
    return Path(os.environ.get("APPDATA", "")) / "HBuilder X" / "HBuilder X.ini"


def parse_registered_paths(text: str) -> list[tuple[int, Path]]:
    """从 HBuilderX.ini 提取 projects\\N\\path；保持编号便于排障。"""
    found: list[tuple[int, Path]] = []
    for raw in text.splitlines():
        match = _PROJECT_PATH_RE.match(raw.strip())
        if match:
            found.append((int(match.group(1)), Path(match.group(2).strip())))
    return found


def _norm(path: Path) -> str:
    return os.path.normcase(os.path.abspath(str(path))).replace("\\", "/").rstrip("/")


def inspect_client_project(
    expected: Path = DEFAULT_EXPECTED,
    registry: Path | None = None,
) -> tuple[bool, str]:
    """返回 (是否唯一且指向当前项目, 人类可读证据/修复提示)。"""
    registry = registry or default_registry_path()
    if not registry.exists():
        return (
            False,
            f"HBuilderX 项目注册表不存在：{registry}。"
            "无法证明 `--project client` 指向当前源码，拒绝编译/打包。",
        )

    paths = parse_registered_paths(registry.read_text(encoding="utf-8", errors="replace"))
    clients = [(idx, p) for idx, p in paths if p.name.casefold() == "client"]
    expected_norm = _norm(expected)
    client_norm = [(idx, p, _norm(p)) for idx, p in clients]
    matches = [(idx, p) for idx, p, norm in client_norm if norm == expected_norm]
    conflicts = [(idx, p) for idx, p, norm in client_norm if norm != expected_norm]

    if len(clients) != 1:
        detail = ", ".join(f"#{idx}={p}" for idx, p in clients) or "无"
        return (
            False,
            f"HBuilderX 中有 {len(clients)} 个名为 `client` 的项目：{detail}。"
            "旧版本必须按 APK versionCode 重命名为 client-<versionCode>，"
            "只保留当前源码为 client。",
        )
    if not matches:
        return (
            False,
            f"唯一的 `client` 未指向当前仓库：期望 {expected}；实际 {clients[0][1]}。",
        )
    if conflicts:
        detail = ", ".join(f"#{idx}={p}" for idx, p in conflicts)
        return False, f"发现非当前 `client` 项目：{detail}"

    idx, actual = clients[0]
    return True, f"# {idx} client -> {actual}"


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 HBuilderX 的 client 项目是否唯一且指向当前源码")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--registry", type=Path, default=None)
    args = parser.parse_args()

    ok, detail = inspect_client_project(args.expected, args.registry)
    print(("[OK] " if ok else "[FAIL] ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
