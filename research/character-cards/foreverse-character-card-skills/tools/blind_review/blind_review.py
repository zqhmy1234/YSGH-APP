#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""双盲评审 harness：让 LLM 评委盲评「哪段更像真人写的」（AB/BA 双顺序）。

这是 docs/blind-review-experiment.md 实验的评审脚本（去内部网关版）。任何
OpenAI 兼容端点都能跑；语料自备（本仓库不含实验用的社区人类卡，见实验报告
「Honest limits」节）。语料格式：

    {"texts": {"id1": "开场白正文……", "id2": "……"},
     "pairs": [{"id": "g1", "kind": "gold", "x": "id1", "y": "id2"}]}

prompt 变体（BLIND_MODE）：
  neutral   —— 只问「哪段更像真人」，零判据（实验里 gold 正确率 12%）
  antibias  —— 显式反偏（唯一把 claude 系拉到 83% 的配置；对多数基座无效）
  fewshot   —— 三个真实读者判例校准（实验实测 58%，弱于 antibias）

用法：
  export OPENAI_BASE_URL=https://api.example.com/v1  OPENAI_API_KEY=sk-...
  BLIND_JUDGE=<model-id> BLIND_MODE=antibias \
      python3 tools/blind_review/blind_review.py corpus.json out.jsonl

结果统计（gold 正确率/位置稳定性/评委间一致率）自行按 jsonl 聚合；实验设计
纪律：每对必须双顺序各判一次，位置翻转要单独报告。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

BASE = os.environ["OPENAI_BASE_URL"].rstrip("/")
KEY = os.environ["OPENAI_API_KEY"]
JUDGE = os.environ["BLIND_JUDGE"]
MODE = os.environ.get("BLIND_MODE", "neutral")

HEAD = "下面是两段中文角色卡的开场白正文（A 与 B，均为节选，人名已统一替换为化名，排版组件已去除）。两段来自不同的创作者。\n\n"

TASK = {
    "neutral": "你的任务：凭你自己的判断，指出哪一段更像「真人创作者」亲手写的，哪一段更像 AI 生成的。直接凭语感判断。",
    "antibias": (
        "你的任务：判断哪一段更像「真人网文/乙女游戏社区创作者」亲手写的。\n"
        "重要校准（这些是 LLM 评委最常犯的错，务必反过来想）：\n"
        "1. 细节密度高、信息紧凑、每个细节都呼应人设 → 这是 AI 特征，不是人味。真人写作有大量「白长的」闲笔。\n"
        "2. 带状态栏/面板/内心 OS 排版 → 中性信号，AI 和真人都爱用，不作为人味证据。\n"
        "3. 句句工整、处处点题、修辞漂亮 → AI 特征。真人段落长短不一、偶有语病、密度不均。\n"
        "4. 「俗套/狗血」的桥段（霸总、白月光、追妻火葬场）→ 这恰恰是真人社区的主流质感，俗 ≠ 假，不要因为俗就判 AI。\n"
        "5. 「优雅精致」反而可疑——真人有毛边，AI 才光滑。"
    ),
    "fewshot": (
        "你的任务：判断哪一段更像「真人创作者」亲手写的。先看三个真实标注范例校准你的判断：\n\n"
        "【范例1·这是 AI 生成的典型】「大二摔栏摔的，她说那是「学费」。」——每个设定都让角色说句俏皮话盖章，句句点题、细节全服务人设，这是 AI 味。\n"
        "【范例2·这是 AI 生成的典型】「骂人不带脏字，全是事实句。」——工整的对仗翻转 + 生造概念词，AI 特征。\n"
        "【范例3·这是真人写的典型】法拉利+白月光+追妻火葬场的俗套狗血桥段，语言不算精致甚至有点土——但松弛、有闲笔、密度不均，这是真人社区主流。\n\n"
        "校准要点：真人有毛边和闲笔，AI 光滑且句句服务设定；俗 ≠ 假。"
    ),
}[MODE]

PROMPT = HEAD + TASK + """

【A】
{a}

【B】
{b}

只输出一行 JSON：{{"verdict": "A"/"B"/"tie"（更像真人的那段）, "confidence": 1-5, "reason": "≤40字"}}"""


def call(content: str, retries: int = 3) -> str:
    body = {"model": JUDGE, "messages": [{"role": "user", "content": content}],
            "max_tokens": 4000, "temperature": 0.2}
    req = urllib.request.Request(
        f"{BASE}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
    )
    for i in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read())
            t = d["choices"][0]["message"]["content"] or ""
            if t.strip():
                return t
        except Exception:
            if i == retries - 1:
                return ""
        time.sleep(2)
    return ""


def parse(text: str):
    m = re.search(r'\{[^{}]*"verdict"[^{}]*\}', text, re.DOTALL)
    if not m:
        return None
    try:
        d = json.loads(m.group(0))
        v = str(d.get("verdict", "")).strip().upper()
        if v not in ("A", "B", "TIE"):
            return None
        return {"verdict": v, "confidence": int(d.get("confidence", 3)), "reason": str(d.get("reason", ""))[:60]}
    except Exception:
        return None


def main() -> int:
    corpus = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out = Path(sys.argv[2])
    rows = []
    for pair in corpus["pairs"]:
        for order in ("orig", "swap"):
            a, b = (pair["x"], pair["y"]) if order == "orig" else (pair["y"], pair["x"])
            v = parse(call(PROMPT.format(a=corpus["texts"][a], b=corpus["texts"][b])))
            picked = {"A": a, "B": b}.get(v["verdict"], "tie") if v else None
            rows.append({"judge": JUDGE, "mode": MODE, "pair": pair["id"], "kind": pair.get("kind", ""),
                         "order": order, "slotA": a, "slotB": b, "picked": picked,
                         "confidence": v["confidence"] if v else None,
                         "reason": v["reason"] if v else "PARSE_FAIL"})
            print(f"[{JUDGE}|{MODE}] {pair['id']}/{order} → {picked}")
    out.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n", encoding="utf-8")
    print(f"OK {out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
