#!/usr/bin/env python3
"""碎句指纹普查：把成稿的「碎句腔」六项指标与真人书区间做对照验收。

配套 style-anchor-fusion skill §5.5「碎句腔警戒」：逗号剁碎的「碎句文艺腔」
（「碑不高，四尺，碑身黑」「今夜，是他的手了」「入夜，布防。」型）是部分模型自带的
笔癖——对照实验实锤：零语料裸写稿与深读过 74 万字语料的融合稿碎句指纹同档，读真书
不会矫正句子形状；而真人书的叙述基底是中长句，碎句只偶尔打在情绪峰值上。所以句式层
要靠任务书里的显式禁令管、写后靠本脚本对真人书区间验收。

指标（全部在剥离引号内对话后的叙述文本上计算——碎句腔是叙述层现象，
对话占比差异会稀释信号；每万字归一化）：
  1. frag_le4_pct   极短小句占比：按 ，。！？；…： 切分后 ≤4 字小句的百分比
  2. avg_clause_len 平均小句长（字）
  3. loc_time_lead  句首「短语(≤4字，以时间/方位语素收尾)＋逗号」率 / 万字
                    （「雾里，」「今夜，」「入夜，」型设景/悬置开头）
  4. tiny_sent      一至二字独词句率 / 万字（「黑。」「冷。」「等。」型）
  5. bare_quant     裸数量词同位插入率 / 万字（「，四尺，」「，七十步，」型：
                    两逗号间 ≤4 字且为 纯数字+量词 形态。注意：递进列举
                    「一圈，两圈，三圈」也会计入，真人书里这种形态合法，
                    命中后用 --examples 人工分拣同位插入与递进）
  6. dangling_scene 悬空设景独立段率 / 万字（整段 ≤14 字、恰一个逗号、
                    句号/叹号收尾——「当天夜里，灯房。」型近似；含少量
                    非设景短段，与真人书同尺可比）

告警线（三条，出自实验对 16 本真人网文的叙述层实测；当时能把 8 部机器稿里的
7 部与全部 5 本参照真人书分开）：
  裸数量词同位插入 > 0        —— 真人书实测恒为 0
  1-2 字独词句 > 5 条/万字     —— 真人带 0.1~2.1
  悬空设景段 > 20 段/万字      —— 真人带 3~18

区间来自该实验的中文网文语料实测；**语料/题材/语言换了要用 --anchor 现场重锚**，
别拿内置线当万题材通用（本脚本正则为中文专用）。

用法:
  fragment_census.py --target 成稿目录或文件 [更多成稿...]
                     [--anchor 真人书.txt ...] [--examples] [--no-gate]

  --target   成稿（目录递归收 .md/.txt，或单文件），每个目标出一行指标
  --anchor   可选：真人书语料文件或目录，同表并排输出（现场重锚区间用，不参与闸门）
  --examples 附每个目标的 裸数量同位/悬空设景/独词句 命中样例
  --no-gate  只出表不设退出码

退出码：任一 --target 触告警=1，全过=0（可直接当产线闸门用）。
"""
import argparse
import os
import re
import sys

# 引号形态自适应：弯引号/直角引号/书名号式直角引号三形态成对剥除
QUOTE_PAIRS = [("\u201c", "\u201d"), ("\u300c", "\u300d"), ("\u300e", "\u300f")]

CLAUSE_SPLIT = re.compile(r"[，。！？；…：]+")
HAN = re.compile(r"[\u4e00-\u9fff]")

# 时间/方位语素（短语收尾字）——「雾里，」「今夜，」「窗外，」「入夜，」「三更，」
LOC_TIME_TAIL = "里外中间处上下前后头边夜晨昏晓更时刻旁"
LOC_TIME_LEAD = re.compile(
    r"(?:^|[。！？…；])([\u4e00-\u9fff]{1,4}[" + LOC_TIME_TAIL + r"])，"
)

CN_NUM = "一二三四五六七八九十百千两半"
# 裸数量词：纯 数字+量词（≤4字），如 四尺/七十步/三丈/一寸/两间/百步
BARE_QUANT = re.compile(
    r"，([" + CN_NUM + r"][" + CN_NUM + r"]?[尺寸丈里步间只条根张扇口座层文钱斤声更息拍指眼])，"
)

TINY_SENT = re.compile(r"(?:^|[。！？…；，])([\u4e00-\u9fff]{1,2})。")

# 告警线（实验对 16 本真人网文的实测区间派生；换语料用 --anchor 重锚）
GATE_QUANT_PER_WAN = 0.0    # 裸数量同位：真人书恒 0，>0 告警
GATE_TINY_PER_WAN = 5.0     # 1-2 字独词句：真人带 0.1~2.1，>5 告警
GATE_DANGLING_PER_WAN = 20.0  # 悬空设景段：真人带 3~18，>20 告警


def strip_dialogue(text: str) -> str:
    """剥引号内对话（含引号本身）。

    替换为换行而不是空串：直接删除会把引号两侧的叙述拼接出原文不存在的
    形态（实测教训：剥空串会伪造出不存在的裸数量同位命中）。
    """
    for lo, hi in QUOTE_PAIRS:
        text = re.sub(
            re.escape(lo) + r"[^" + re.escape(lo) + re.escape(hi) + r"]*" + re.escape(hi),
            "\n",
            text,
        )
    # 半角直引号：成对最小匹配
    text = re.sub(r'"[^"\n]*"', "\n", text)
    return text


def collect_files(target: str):
    if os.path.isfile(target):
        return [target]
    files = []
    for root, _dirs, names in os.walk(target):
        for n in sorted(names):
            if n.endswith((".md", ".txt")):
                files.append(os.path.join(root, n))
    files.sort()
    if not files:
        sys.exit(f"[err] {target} 下没有 .md/.txt 文件")
    return files


def load_text(target: str) -> str:
    parts = []
    for p in collect_files(target):
        with open(p, encoding="utf-8", errors="ignore") as f:
            t = f.read()
        if p.endswith(".md"):
            # 剥章题行
            t = re.sub(r"^#.*$", "", t, flags=re.M)
        parts.append(t)
    return "\n".join(parts)


def census(label: str, kind: str, target: str, want_examples: bool):
    raw = load_text(target)
    narr = strip_dialogue(raw)
    han_chars = len(HAN.findall(narr))
    wan = han_chars / 10000.0 if han_chars else 1.0

    clauses = [c for c in (s.strip() for s in CLAUSE_SPLIT.split(narr)) if HAN.search(c or "")]
    clause_lens = [len(HAN.findall(c)) for c in clauses]
    le4 = sum(1 for n in clause_lens if n <= 4)
    frag_pct = 100.0 * le4 / len(clause_lens) if clause_lens else 0.0
    avg_len = sum(clause_lens) / len(clause_lens) if clause_lens else 0.0

    loc_hits = LOC_TIME_LEAD.findall(narr)
    quant_hits = BARE_QUANT.findall(narr)
    tiny_hits = TINY_SENT.findall(narr)

    dangling = []
    for line in narr.splitlines():
        s = line.strip()
        if not s or len(s) > 14:
            continue
        if s.count("，") == 1 and s[-1] in "。！" and HAN.search(s):
            dangling.append(s)

    row = {
        "label": label,
        "kind": kind,
        "han_wan": han_chars / 10000.0,
        "frag_le4_pct": frag_pct,
        "avg_clause_len": avg_len,
        "loc_time_per_wan": len(loc_hits) / wan,
        "tiny_per_wan": len(tiny_hits) / wan,
        "quant_per_wan": len(quant_hits) / wan,
        "dangling_per_wan": len(dangling) / wan,
        "quant_abs": len(quant_hits),
    }
    ex = {
        "quant": sorted(set("，" + q + "，" for q in quant_hits)),
        "dangling": dangling[:12],
        "tiny": sorted(set(t + "。" for t in tiny_hits))[:15],
    } if want_examples else None
    return row, ex


def alerts_for(row):
    out = []
    if row["quant_abs"] > GATE_QUANT_PER_WAN:
        out.append(f"裸数量同位 {row['quant_abs']} 处（真人书恒 0）")
    if row["tiny_per_wan"] > GATE_TINY_PER_WAN:
        out.append(f"独词句 {row['tiny_per_wan']:.2f}/万字（>5 告警，真人带 0.1~2.1）")
    if row["dangling_per_wan"] > GATE_DANGLING_PER_WAN:
        out.append(f"悬空设景段 {row['dangling_per_wan']:.2f}/万字（>20 告警，真人带 3~18）")
    return out


def main():
    ap = argparse.ArgumentParser(description="成稿碎句指纹 vs 真人书区间 对照验收")
    ap.add_argument("--target", nargs="+", required=True, help="成稿目录或文件（可多个）")
    ap.add_argument("--anchor", nargs="*", default=[], help="真人书语料文件/目录（可选，重锚区间用）")
    ap.add_argument("--examples", action="store_true", help="附命中样例")
    ap.add_argument("--no-gate", action="store_true", help="只出表不设退出码")
    args = ap.parse_args()

    def short_label(path: str) -> str:
        parts = [p for p in os.path.normpath(path).split(os.sep) if p not in ("", ".")]
        return "/".join(parts[-2:]) if parts else path

    jobs = [(short_label(t), "target", t) for t in args.target]
    jobs += [(short_label(a), "anchor", a) for a in args.anchor]

    rows, examples = [], {}
    for label, kind, path in jobs:
        if not os.path.exists(path):
            sys.exit(f"[err] 路径不存在: {path}")
        row, ex = census(label, kind, path, args.examples)
        rows.append(row)
        if ex is not None:
            examples[label] = ex

    print("| 文本 | 叙述万字 | ≤4字小句% | 均小句长 | 方位/时间起句/万字 | 1-2字独词句/万字 | 裸数量同位/万字 | 悬空设景段/万字 |")
    print("|---|---|---|---|---|---|---|---|")
    for r in rows:
        tag = "（锚）" if r["kind"] == "anchor" else ""
        print(
            f"| {r['label']}{tag} | {r['han_wan']:.1f} | {r['frag_le4_pct']:.1f} | {r['avg_clause_len']:.1f} "
            f"| {r['loc_time_per_wan']:.1f} | {r['tiny_per_wan']:.2f} | {r['quant_per_wan']:.2f} | {r['dangling_per_wan']:.2f} |"
        )

    gate_failed = False
    for r in rows:
        if r["kind"] != "target":
            continue
        hits = alerts_for(r)
        if hits:
            gate_failed = True
            print(f"\n[告警] {r['label']}：" + "；".join(hits))
    if not gate_failed:
        print("\n三条告警线全过（内置区间来自实验语料实测，换语料/题材请用 --anchor 重锚复核）。")

    if args.examples:
        for label, ex in examples.items():
            print(f"\n### {label}")
            print("裸数量同位命中：", "、".join(ex["quant"]) if ex["quant"] else "（零）")
            print("悬空设景段样例：", " / ".join(ex["dangling"]) if ex["dangling"] else "（零）")
            print("1-2字独词句样例：", " ".join(ex["tiny"]) if ex["tiny"] else "（零）")

    sys.exit(1 if (gate_failed and not args.no_gate) else 0)


if __name__ == "__main__":
    main()
