"""性能优化卡 PF 资产工具（可复跑，不依赖网络）。

子命令：
  fonts     对 client/static/fonts/SarasaGothicSC-{Regular,SemiBold,Bold}.ttf 子集化，
            原件备份到 scripts/pf_backup/（永久从备份出发，重复运行结果幂等）
  hero      将 client/static/hero-*.jpg 压缩到 <150K/张（质量 80 起每次 -5 渐进降，
            保持尺寸，原件备份到 scripts/pf_backup/）
  manifest  client/manifest.json 的 abiFilters 仅保留 arm64-v8a（二进制读写保 UTF-8 BOM）
  check     仅做 cmap 自验与统计，不改任何文件

字符集 = client/**/*.{uvue,uts} 扫描集 ∪ ASCII ∪ 常用标点 ∪ GB2312 一级汉字(3755)。
不碰任何 .uvue/.uts：子集产物覆盖原文件名，loadFont 路径不变。
"""
import io
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "client"
STATIC = CLIENT / "static"
FONTS_DIR = STATIC / "fonts"
BACKUP = ROOT / "scripts" / "pf_backup"

FONTS = [
    "SarasaGothicSC-Regular.ttf",
    "SarasaGothicSC-SemiBold.ttf",
    "SarasaGothicSC-Bold.ttf",
]
HERO_LIMIT = 150 * 1024

ASCII = {chr(i) for i in range(0x20, 0x7F)} | {"\n", "\r", "\t"}
PUNCT = set(
    "，。、；：？！…—～·ˉ¨〃〆〇〈〉《》「」『』【】〔〕〖〗［］｛｝（）"
    "！＂＃＄％＆＇＊＋，－．／：；＜＝＞？＠＼＾＿｀｜｀｛｜｝～"
    "“”‘’‚„…‥·′″‰℃℉°×÷±≠≈≡≤≥∞√∑∏∈∴∵∶"
    "•●○◎⊙■□▲△▼▽◆◇★☆✓✔✕✗※→←↑↓⇒"
)

def gb2312_level1():
    """GB2312 一级汉字（区位 16-55，3755 字），确定性生成常用字表。"""
    out = set()
    for qu in range(16, 56):
        for wei in range(1, 95):
            try:
                out.add(bytes((0xA0 + qu, 0xA0 + wei)).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return out

def scanned_chars():
    """扫描 client/ 全部 .uvue/.uts 文本字符。"""
    chars = set()
    for pattern in ("*.uvue", "*.uts"):
        for p in CLIENT.rglob(pattern):
            try:
                chars |= set(p.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                continue
    return chars

def required_codepoints():
    req = scanned_chars() | ASCII | PUNCT | gb2312_level1()
    return {ord(c) for c in req if ord(c) >= 0x20 and not c.isspace()}

# ---------------------------------------------------------------- fonts

def cmd_fonts():
    from fontTools import subset
    BACKUP.mkdir(parents=True, exist_ok=True)
    req = sorted(required_codepoints())
    print(f"[fonts] required codepoints: {len(req)}")
    for name in FONTS:
        src = FONTS_DIR / name
        bak = BACKUP / name
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f"[fonts] backup: {src.name} -> {bak}")
        before = src.stat().st_size
        opts = subset.Options()
        opts.flavor = None
        opts.name_IDs = ["*"]
        opts.notdef_outline = True
        font = subset.load_font(bak, opts)  # 永远从全量备份出发 → 可复跑
        ss = subset.Subsetter(opts)
        ss.populate(unicodes=req)
        ss.subset(font)
        tmp = src.with_suffix(".tmp.ttf")
        subset.save_font(font, str(tmp), opts)
        os.replace(tmp, src)
        after = src.stat().st_size
        print(
            f"[fonts] {name}: {before} -> {after} bytes "
            f"({before / 1048576:.2f}MB -> {after / 1048576:.2f}MB, "
            f"-{(1 - after / before) * 100:.1f}%)"
        )
    return cmd_check()

def cmd_check():
    from fontTools.ttLib import TTFont
    req = required_codepoints()
    ok = True
    for name in FONTS:
        sub = TTFont(str(FONTS_DIR / name))
        cmap = set(sub.getBestCmap().keys())
        full_path = BACKUP / name
        if full_path.exists():
            full = set(TTFont(str(full_path)).getBestCmap().keys())
            lost = sorted((req & full) - cmap)  # 全量字体有、子集却丢了 → 必须为 0
            never = sorted(req - full)          # 原字体本就没有 → 回落系统字体（emoji 等）
            print(
                f"[check] {name}: cmap={len(cmap)} (full={len(full)}), "
                f"required_lost={len(lost)}, not_in_font={len(never)}"
            )
            if lost:
                ok = False
                print(f"[check]   LOST sample: {''.join(chr(c) for c in lost[:50])!r}")
        else:
            print(f"[check] {name}: cmap={len(cmap)} (no full backup, skip diff)")
    print(f"[check] result: {'PASS' if ok else 'FAIL'}")
    return ok

# ---------------------------------------------------------------- hero

def cmd_hero():
    from PIL import Image
    BACKUP.mkdir(parents=True, exist_ok=True)
    for src in sorted(STATIC.glob("hero-*.jpg")):
        bak = BACKUP / src.name
        if not bak.exists():
            shutil.copy2(src, bak)
            print(f"[hero] backup: {src.name} -> {bak}")
        img = Image.open(str(bak))
        w, h = img.size
        chosen = None
        for q in range(80, 25, -5):
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=q, optimize=True, progressive=True)
            data = buf.getvalue()
            chosen = (q, data)
            if len(data) <= HERO_LIMIT:
                break
        q, data = chosen
        src.write_bytes(data)
        flag = "" if len(data) <= HERO_LIMIT else "  WARN: still >150K"
        print(
            f"[hero] {src.name}: {bak.stat().st_size} -> {len(data)} bytes, "
            f"quality={q}, size={w}x{h}{flag}"
        )

# ---------------------------------------------------------------- manifest

def cmd_manifest():
    p = CLIENT / "manifest.json"
    raw = p.read_bytes()
    has_bom = raw.startswith(b"\xef\xbb\xbf")
    data = json.loads(raw.decode("utf-8-sig"))
    android = data["app"]["distribute"]["android"]
    print(f"[manifest] abiFilters before: {android.get('abiFilters')}")
    android["abiFilters"] = ["arm64-v8a"]
    out = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    p.write_bytes((b"\xef\xbb\xbf" if has_bom else b"") + out.encode("utf-8"))
    print(f"[manifest] abiFilters after: ['arm64-v8a'], BOM kept: {has_bom}")

# ---------------------------------------------------------------- main

def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check"
    if cmd == "fonts":
        cmd_fonts()
    elif cmd == "hero":
        cmd_hero()
    elif cmd == "manifest":
        cmd_manifest()
    elif cmd == "check":
        cmd_check()
    else:
        print(f"unknown command: {cmd}")
        return 2
    return 0

if __name__ == "__main__":
    sys.exit(main())
