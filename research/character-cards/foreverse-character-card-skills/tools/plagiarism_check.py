#!/usr/bin/env python3
"""防抄袭滑窗自查：把仿写成稿与语料书做 N 字滑窗重合检查（默认 15 字）。

口径：只保留汉字（剔标点/空白/数字/英文/markdown 符号）后做滑窗——比「含标点原文」
更严（标点改写掩盖不了抄袭），零命中的结论才硬。中文语料专用；英文场景把 HAN 正则
换成小写词元流（word-level n-gram）即可，思路相同。

配套 style-anchor-fusion skill 的防抄袭红线：仿写学的是「声」（节奏/腔调/手法），
不是「字」。任何 >= 窗口长度的连续原文重合都算红线，须改写或删除后复跑到零命中。

用法:
  plagiarism_check.py --corpus 语料1.txt [语料2.txt ...] --target 成稿目录或文件 [--win 15] [--sample N]

  --corpus   一个或多个语料文件（txt/md），即写前深读的那几本书
  --target   成稿目录（递归收 .md/.txt，按文件名排序）或单个成稿文件
  --win      滑窗字数，默认 15
  --sample   只抽 N 个成稿文件均匀分布检查（含首尾；如 5 = 首/¼/中/¾/末）；缺省全量

退出码：有命中=1，零命中=0（可直接当产线闸门用）。
内存量级参考：语料每 10 万字约产 10 万个 gram（几十 MB 级），2~3 本指派语料无压力；
不要拿整个几千万字的书库当 --corpus。
"""
import argparse
import os
import re
import sys

HAN = re.compile(r"[\u4e00-\u9fff]")


def han_stream(text: str) -> str:
    return "".join(HAN.findall(text))


def build_ngram_set(paths, win):
    grams = set()
    for p in paths:
        with open(p, encoding="utf-8") as f:
            s = han_stream(f.read())
        for i in range(len(s) - win + 1):
            grams.add(s[i:i + win])
    return grams


def collect_targets(target, sample):
    if os.path.isfile(target):
        return [target]
    files = []
    for root, _dirs, names in os.walk(target):
        for n in sorted(names):
            if n.endswith((".md", ".txt")):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        sys.exit(f"[err] {target} 下没有 .md/.txt 成稿文件")
    if sample and sample < len(files):
        n = len(files)
        # 均匀取样且必含首尾（sample=5 即 首/¼/中/¾/末）
        idxs = sorted({round(i * (n - 1) / (sample - 1)) for i in range(sample)})
        files = [files[i] for i in idxs]
    return files


def main():
    ap = argparse.ArgumentParser(description="仿写成稿 vs 语料书 滑窗重合自查")
    ap.add_argument("--corpus", nargs="+", required=True, help="语料文件（可多个）")
    ap.add_argument("--target", required=True, help="成稿目录或单文件")
    ap.add_argument("--win", type=int, default=15, help="滑窗字数（默认 15）")
    ap.add_argument("--sample", type=int, default=None, help="只抽 N 个成稿文件（缺省全量）")
    args = ap.parse_args()

    for p in args.corpus:
        if not os.path.isfile(p):
            sys.exit(f"[err] 语料文件不存在: {p}")

    print(f"[build] {args.win}-gram 索引（纯汉字流）...", file=sys.stderr)
    grams = build_ngram_set(args.corpus, args.win)
    print(f"[build] 语料 {len(args.corpus)} 本 = {len(grams):,} grams", file=sys.stderr)

    targets = collect_targets(args.target, args.sample)
    print(f"## 防抄袭滑窗自查（win={args.win} · 纯汉字流 · {len(targets)} 个成稿文件）\n")
    print("| 文件 | 窗口数 | 命中数 | 命中样例 |")
    print("|---|---|---|---|")
    grand_total, grand_hits = 0, 0
    for path in targets:
        with open(path, encoding="utf-8") as f:
            s = han_stream(f.read())
        total, hits, samples = 0, 0, []
        for i in range(len(s) - args.win + 1):
            total += 1
            g = s[i:i + args.win]
            if g in grams:
                hits += 1
                if len(samples) < 3:
                    samples.append(g)
        grand_total += total
        grand_hits += hits
        rel = os.path.basename(path)
        print(f"| {rel} | {total:,} | **{hits}** | {'；'.join(samples) if samples else '—'} |")
    print(f"\n合计：{grand_total:,} 窗口，命中 **{grand_hits}**。"
          + ("零命中，通过。" if grand_hits == 0 else "有命中=红线，逐处改写后复跑。"))
    sys.exit(1 if grand_hits else 0)


if __name__ == "__main__":
    main()
