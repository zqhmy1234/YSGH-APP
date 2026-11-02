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
    """返回 (是否唯一且指向当前项目, 人类可读证据/修复提示)。

    ⚠️ 2026-10-30：注册表里会残留**已删除的旧 worktree** 条目（本机实测 5 条里有 4 条目录
    早已不存在）。这类死条目 CLI 根本编译不到，但原实现把它们一并算作「同名冲突」，
    导致编译门永久报环境错误、任何改动都无法真编译验证（等于门禁被自己的历史噪声锁死）。
    现口径：**只有磁盘上仍存在的注册路径参与唯一性判定**；死条目在 detail 里如实列出，
    不再阻断。存活路径的唯一性与指向必须严格成立（原判定一个都不放松）。
    """
    registry = registry or default_registry_path()
    if not registry.exists():
        return (
            False,
            f"HBuilderX 项目注册表不存在：{registry}。"
            "无法证明 `--project client` 指向当前源码，拒绝编译/打包。",
        )

    paths = parse_registered_paths(registry.read_text(encoding="utf-8", errors="replace"))
    registered_clients = [(idx, p) for idx, p in paths if p.name.casefold() == "client"]
    dead = [(idx, p) for idx, p in registered_clients if not p.exists()]
    clients = [(idx, p) for idx, p in registered_clients if p.exists()]
    dead_note = ""
    if dead:
        dead_note = (
            f"（已忽略 {len(dead)} 条磁盘上不存在的死条目："
            f"{', '.join(f'#{idx}={p}' for idx, p in dead)}）"
        )

    expected_norm = _norm(expected)
    client_norm = [(idx, p, _norm(p)) for idx, p in clients]
    matches = [(idx, p) for idx, p, norm in client_norm if norm == expected_norm]
    conflicts = [(idx, p) for idx, p, norm in client_norm if norm != expected_norm]

    if len(clients) != 1:
        detail = ", ".join(f"#{idx}={p}" for idx, p in clients) or "无"
        return (
            False,
            f"HBuilderX 中有 {len(clients)} 个**存在的**名为 `client` 的项目：{detail}。"
            "旧版本必须按 APK versionCode 重命名为 client-<versionCode>，"
            "只保留当前源码为 client。" + dead_note,
        )
    if not matches:
        return (
            False,
            f"唯一的 `client` 未指向当前仓库：期望 {expected}；实际 {clients[0][1]}。" + dead_note,
        )
    if conflicts:
        detail = ", ".join(f"#{idx}={p}" for idx, p in conflicts)
        return False, f"发现非当前 `client` 项目：{detail}" + dead_note

    idx, actual = clients[0]
    return True, f"# {idx} client -> {actual}" + dead_note


def _selftest() -> int:
    """负向探针自校验（项目纪律：新增/修改门禁必须证明它真的会拦）。

    用临时目录构造注册表与「项目目录」，覆盖四种判定，全部符合预期才返回 0。
    """
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        cur = root / "repo" / "client"
        other = root / "old" / "client"
        deadpath = root / "gone" / "client"
        cur.mkdir(parents=True)
        other.mkdir(parents=True)
        reg = root / "HBuilder X.ini"

        def run(paths: list[Path]) -> tuple[bool, str]:
            body = "\n".join(f"projects\\{i}\\path={p}" for i, p in enumerate(paths))
            reg.write_text(body, encoding="utf-8")
            return inspect_client_project(cur, reg)

        ok, detail = run([cur])
        if not ok:
            failures.append(f"情形1（唯一且正确）应通过，实际：{detail}")

        ok, detail = run([cur, other])
        if ok:
            failures.append("情形2（两个都存在且同名）应拦截，实际放行")

        ok, detail = run([other])
        if ok:
            failures.append("情形3（唯一但指向别处）应拦截，实际放行")

        ok, detail = run([cur, deadpath])
        if not ok:
            failures.append(f"情形4（旧条目目录已不存在）应放行，实际：{detail}")

    if failures:
        for f in failures:
            print("[FAIL] " + f)
        return 1
    print("[OK] 负向探针 4/4 通过（唯一通过 / 双存在拦截 / 指向别处拦截 / 死条目放行）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="检查 HBuilderX 的 client 项目是否唯一且指向当前源码")
    parser.add_argument("--expected", type=Path, default=DEFAULT_EXPECTED)
    parser.add_argument("--registry", type=Path, default=None)
    parser.add_argument("--selftest", action="store_true", help="负向探针自校验（不读真实注册表）")
    args = parser.parse_args()

    if args.selftest:
        return _selftest()

    ok, detail = inspect_client_project(args.expected, args.registry)
    print(("[OK] " if ok else "[FAIL] ") + detail)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
