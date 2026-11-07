#!/usr/bin/env python3
"""把产品部给来的 C 批音频收进真值目录，并按「组号-4位序号」+ 真实容器扩展名重命名。

【为什么需要这个脚本】
产品部每次给音频都是**一个文件夹一个说法**：
  · `素材/C批a组测试数据-语音片段/a001.mp3`   —— 编号没有横杠，扩展名还是错的（实际是 M4A）
  · `素材/c批a组数据2.0/a-0006.mp3`           —— 编号有横杠，扩展名同样错（实际是 AAC/M4A）
  · `素材/C批音频/d-0001.mp3`                 —— 真 MP3，但标注表里写的是 `d001.mp3`
而 `convert_truth_xlsx.py` 按 `research/truth-data/c/audio/<file_name>` 登记 `audio_ref`，
文件名对不上就记 `MISSING_MEDIA_FILE`（只是告警，转录仍能用，但 WER 校准就没音频了）。

本脚本做三件事：
  1. **嗅探真实容器**（不看扩展名，看文件头）→ 定成 .m4a / .mp3 / .wav / .flac
  2. **统一命名**为 `<组字母>-<4位序号>.<真实扩展名>`（`a001` → `a-0001`、`d1` → `d-0001`）
  3. **只在内容不同才覆盖**（同尺寸视为已同步，避免每次重拷 50M）

【用法】
    python scripts/sync_c_audio.py                  # 收 <工作区>/真值数据/素材 下的全部
    python scripts/sync_c_audio.py --dry-run        # 只打印会怎么改
    python scripts/sync_c_audio.py --src <目录> --dst research/truth-data/c/audio

退出码：0 = 完成；1 = 有源文件无法识别编号或容器（不会写入）。
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
from pathlib import Path

# 只收这些容器，其余（txt/json/截图）一律忽略
AUDIO_SUFFIXES = {".mp3", ".m4a", ".wav", ".flac", ".aac", ".ogg", ".amr", ".opus"}

# 组字母 + 可选撇号 + 序号：`a001` / `a-0006` / `a'-0003` / `D1`
_NAME_RE = re.compile(r"^(?P<grp>[A-Za-z])['’]?[-_\s]?(?P<num>\d+)$")


def sniff_container(path: Path) -> str | None:
    """按文件头判断真实容器，返回规范扩展名（含点）；判断不出返回 None。

    扩展名在素材里基本不可信（`a001.mp3` 实际是 MP4/M4A 容器），
    所以只认魔数：
      · `....ftyp`      → MP4 家族（M4A）
      · `ID3` / 0xFFEx  → MP3
      · `RIFF....WAVE`  → WAV
      · `fLaC`          → FLAC
    """
    with path.open("rb") as fh:
        head = fh.read(12)
    if len(head) < 12:
        return None
    if head[4:8] == b"ftyp":
        return ".m4a"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return ".wav"
    if head[:4] == b"fLaC":
        return ".flac"
    if head[:3] == b"ID3":
        return ".mp3"
    if head[0] == 0xFF and (head[1] & 0xE0) == 0xE0:
        return ".mp3"
    return None


def canonical_name(path: Path) -> tuple[str, str] | None:
    """返回 `(组字母, 4 位序号)`；编号不合规返回 None（如 `测试音频1.mp3`）。"""
    m = _NAME_RE.match(path.stem.strip())
    if not m:
        return None
    return m.group("grp").lower(), f"{int(m.group('num')):04d}"


def build_manifest(src_root: Path, dst_dir: Path, mapping: dict[str, Path]) -> list[dict]:
    """按 dst 目录实况重建 `_media_manifest.json`（不累积历史，永远反映当前）。

    每条记源→目的、字节数、以及「表里声明的扩展名 vs 真实容器」不符时的说明，
    这样下次有人问"这个音频哪来的/为什么改名"能直接查到。
    """
    items = []
    for path in sorted(dst_dir.iterdir()):
        if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        src = mapping.get(path.name)
        entry = {
            "dst": f"research/truth-data/c/audio/{path.name}",
            "src": src.relative_to(src_root).as_posix() if src else "",
            "bytes": path.stat().st_size,
            "container": path.suffix.lower(),
        }
        if src is not None and src.suffix.lower() != path.suffix.lower():
            entry["note"] = f"扩展名更正：{src.suffix} → {path.suffix}（按文件头嗅探）"
        items.append(entry)
    return items


def main() -> int:
    parser = argparse.ArgumentParser(description="收 C 批音频并按真实容器重命名")
    default_src = Path(__file__).resolve().parents[2] / "真值数据" / "素材"
    default_dst = Path(__file__).resolve().parents[1] / "research" / "truth-data" / "c" / "audio"
    parser.add_argument("--src", type=Path, default=default_src, help=f"素材根目录（默认 {default_src}）")
    parser.add_argument("--dst", type=Path, default=default_dst, help=f"真值音频目录（默认 {default_dst}）")
    parser.add_argument("--dry-run", action="store_true", help="只打印，不写盘")
    args = parser.parse_args()

    # `测试音频/` 是直播/多人对话，不可作 C 批真值（见 真值数据/README.md），整目录跳过
    skip_dirs = {"测试音频", "测试音频-待标注"}

    sources: list[Path] = []
    for path in sorted(args.src.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in AUDIO_SUFFIXES:
            continue
        if skip_dirs & set(path.parts):
            continue
        sources.append(path)
    if not sources:
        print(f"❌ {args.src} 下没有音频文件")
        return 1

    if not args.dry_run:
        args.dst.mkdir(parents=True, exist_ok=True)

    copied = unchanged = 0
    problems: list[str] = []
    mapping: dict[str, Path] = {}  # dst 文件名 -> 源文件
    for src in sources:
        ident = canonical_name(src)
        container = sniff_container(src)
        if ident is None or container is None:
            problems.append(f"{src.relative_to(args.src)}："
                            f"{'编号不合规' if ident is None else '容器识别失败'}")
            continue
        dst = args.dst / f"{ident[0]}-{ident[1]}{container}"
        mapping[dst.name] = src
        rel = src.relative_to(args.src).as_posix()
        if dst.exists() and dst.stat().st_size == src.stat().st_size:
            unchanged += 1
            print(f"  = {rel:52s} → {dst.name}（已存在，尺寸一致，跳过）")
            continue
        print(f"  + {rel:52s} → {dst.name}{'（dry-run）' if args.dry_run else ''}")
        if not args.dry_run:
            shutil.copy2(src, dst)
        copied += 1

    print(f"\n源文件 {len(sources)} 个：{'将' if args.dry_run else '已'}收 {copied}｜"
          f"已是最新 {unchanged}｜问题 {len(problems)}")

    if not args.dry_run:
        manifest = build_manifest(args.src, args.dst, mapping)
        path = args.dst.parent.parent / "_media_manifest.json"
        path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(f"媒体清单：{path}（{len(manifest)} 条）")
    if problems:
        print("\n⚠️ 以下文件没处理（需人工确认编号/格式）：")
        for line in problems:
            print(f"  - {line}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
