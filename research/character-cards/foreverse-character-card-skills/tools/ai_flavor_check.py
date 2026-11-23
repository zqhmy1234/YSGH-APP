#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Standalone AI-flavor check for Chinese roleplay prose (openings, narration, card copy).

对任意中文叙事文本跑 AI 味检测（不要求完整 card.md）：黑名单套话 + 句式病灶配额制。
检测哲学（gold 判例集校准，详见 docs/blind-review-experiment.md）：
  - 孤立套话词降权——「一丝凉意」人类也写，不判死刑；
  - 句式模式高权重——引语点题 / 对仗翻转 / 生造概念词 / 伪精确数字才是判别力所在；
  - 5 分制扣分：5.0 = 无命中；<3.5 = AI 味过重。

用法：
    python3 tools/ai_flavor_check.py file1.txt file2.md ...
    echo "她张了张嘴，眼中闪过一丝不易察觉的慌乱。" | python3 tools/ai_flavor_check.py -
    python3 tools/ai_flavor_check.py --json file.txt

注意：检测器只覆盖「已知病灶」，满分 ≠ 有人味——细节密度均匀/零闲笔/结构完美这类
整体纹理层的 AI 味，规则抓不到（双盲实验同样证明 LLM 评委也抓不到），人工抽读不可省。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from score_card import ai_flavor_scan  # noqa: E402


def check_text(name: str, text: str, as_json: bool) -> dict:
    r = ai_flavor_scan(first_prose=text)
    out = {
        "input": name,
        "score": r["score"],
        "verdict": "heavy-ai-flavor" if r["score"] < 3.5 else ("some-hits" if r["score"] < 5 else "clean"),
        "flavor_hits": r["ai_flavor_hits"],
        "prose_tic_hits": r["prose_tic_hits"],
        "dup_ratio": r["dup_ratio"],
    }
    if not as_json:
        print(f"{name}: {r['score']:.1f}/5  [{out['verdict']}]")
        for h in r["ai_flavor_hits"]:
            print(f"  - 套话/句式: {h}")
        for h in r["prose_tic_hits"]:
            print(f"  - 病灶: {h}")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("inputs", nargs="+", help="文本文件路径，或 - 读 stdin")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    results = []
    for item in args.inputs:
        if item == "-":
            results.append(check_text("<stdin>", sys.stdin.read(), args.json))
        else:
            p = Path(item)
            results.append(check_text(str(p), p.read_text("utf-8"), args.json))
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    return 1 if any(r["score"] < 3.5 for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
