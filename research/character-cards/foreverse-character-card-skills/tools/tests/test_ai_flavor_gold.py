#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI 味检测器 gold 回归：改 `score_card.py` 的检测规则后必须跑绿。

判例集 = tools/tests/gold_corpus.json（全部为我方原创卡的开场白）：
  - V1_*  ：被真实读者判 AI 的初稿开场白（AI 味主要藏在 Description/作者的话，
            裸开场白句式层大多干净——这正是检测器要做全卡扫描的原因）；
  - V11_* ：同卡按 §2.6 句式纪律重写后 —— 不得低于对应 V1（重写不能倒退），且不被误杀；
  - G_*   ：未经修饰的生成初稿。

内部校准时还用过「社区人类热门卡」做零误杀基线（人类样本均分不得低于 AI 样本），
该语料不可再分发故不入仓；方法与结果见 docs/blind-review-experiment.md。
本文件锁的是可公开复现的判例：真实用户点名卡必扣分 / 点名病句必命中 /
重写不倒退且不被误杀 / 描景词不误伤 / 仓库成品卡全卡扫描反 AI 味满分。
诚实边界：检测器只覆盖「已知句式病灶」，抓不到「细节密度均匀/零闲笔」的整体纹理层
AI 味（双盲实验证明 LLM 评委同样抓不到）——满分 ≠ 有人味，人工抽读不可省。

跑法（仓库根目录）：
    python3 tools/tests/test_ai_flavor_gold.py         # 内置 runner
    python3 -m pytest tools/tests/ -q                  # 或 pytest
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))
import score_card as sc  # noqa: E402

TEXTS = json.loads((_HERE / "gold_corpus.json").read_text(encoding="utf-8"))["texts"]

AI_KEYS = [k for k in TEXTS if k.startswith(("V1_", "G_"))]
V11_KEYS = [k for k in TEXTS if k.startswith("V11_")]
PAIRS = [("V1_szx", "V11_szx"), ("V1_jcy", "V11_jcy"), ("V1_hys", "V11_hys"),
         ("V1_nfe", "V11_nfe"), ("V1_snz", "V11_snz")]


def scan(key: str) -> dict:
    return sc.ai_flavor_scan(TEXTS[key])


def test_user_flagged_card_penalized():
    """真实用户一眼判 AI 的那张卡（V1_szx），裸开场白也必须有命中且被扣分。"""
    r = scan("V1_szx")
    assert r["score"] < 5.0 and (r["ai_flavor_hits"] or r["prose_tic_hits"]), r


def test_shipped_cards_full_scan_clean():
    """仓库成品卡全卡扫描（Description/作者的话都在内）反 AI 味必须满分。

    这是检测器的真实设计输入——裸开场白抓不到藏在人设八股与作者的话里的句式病灶。
    """
    repo = _HERE.parents[1]
    cards = sorted(repo.glob("cards/*/*/card.md"))
    if not cards:
        print("  (cards/ 为空，跳过全卡扫描锚点)")
        return
    for c in cards:
        r = sc.score_card(sc.parse_card(c))
        assert r["categories"]["反AI味"] == 5, f"{c}: {r['detail']['anti_ai']}"


def test_rewrite_improves():
    """同卡重写版反 AI 味不低于初稿（去 AI 味重写不能倒退）。"""
    for v1, v11 in PAIRS:
        assert scan(v11)["score"] >= scan(v1)["score"], f"{v11} 反 AI 味低于 {v1}"


def test_rewrites_not_massacred():
    """人工重写版不被规则误杀：V11 组均分 ≥4.0（规则激进到连修好的都杀 = 回归）。"""
    avg = sum(scan(k)["score"] for k in V11_KEYS) / len(V11_KEYS)
    assert avg >= 4.0, f"重写版被误杀：avg={avg:.2f}"


def test_named_bad_sentences_caught():
    """真实用户点名的病句：对应句式 tic 检测必须命中（回归锁）。"""
    r1 = sc.ai_flavor_scan("大二摔栏摔的，她说那是「学费」。被撞见就说是补充蛋白质。")
    assert any("引语点题" in h for h in r1["prose_tic_hits"]), r1
    r2 = sc.ai_flavor_scan("她骂人不带脏字，全是事实句。")
    assert any("不带X全是Y" in h for h in r2["prose_tic_hits"]), r2
    r3 = sc.ai_flavor_scan("这瓶牛奶是她的胜利锚点，也是行为道谢的一环。")
    assert any("生造概念词" in h for h in r3["prose_tic_hits"]), r3


def test_human_scenery_words_not_flagged():
    """裸「一丝/一抹」描景不误伤（人类常用）；搭情绪词才命中解说症。"""
    ok = sc.ai_flavor_scan("窗外飘来一丝凉意，天边挂着一抹夕阳。她把外套收进包里。")
    assert not ok["ai_flavor_hits"] and not ok["prose_tic_hits"], ok
    bad = sc.ai_flavor_scan("她垂下眼，语气里带着几分不甘，一丝慌乱一闪而过。")
    assert bad["ai_flavor_hits"] or bad["prose_tic_hits"], bad


def _run():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    fails = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            fails += 1
            print(f"FAIL {t.__name__}: {e}")
    print("\n判例集反 AI 味打分：")
    for grp, keys in [("AI", AI_KEYS), ("重写", V11_KEYS)]:
        avg = sum(scan(k)["score"] for k in keys) / len(keys)
        print(f"  {grp:4s} 均分 {avg:.2f} | " + " ".join(f"{k}={scan(k)['score']:.1f}" for k in keys))
    return fails


if __name__ == "__main__":
    raise SystemExit(1 if _run() else 0)
