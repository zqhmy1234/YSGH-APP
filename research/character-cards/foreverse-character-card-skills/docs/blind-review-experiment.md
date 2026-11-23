# LLM judges rate human-written character cards as AI (12% gold accuracy)

*A blind-review reliability experiment, 2026-07. Scripts in [`tools/blind_review/`](../tools/blind_review/). 中文摘要见文末。*

## TL;DR

We asked LLM judges to tell human-written roleplay character cards from AI-written ones, blind, in pairs. The judges agreed with each other 86% of the time — and got the known-answer pairs right **12%** of the time. They systematically voted *backwards*: AI-written cards that a real reader had instantly clocked as AI got rated "more human", and cards written by humans (top community cards with millions of chat interactions) got rated "AI". An explicit anti-bias prompt fixed one model family (claude: 12% → 83%) and did almost nothing for the others (25–33%). Our conclusion: **an LLM judge that hasn't passed gold calibration must not be used as an optimization target.** We ship a rule-based detector calibrated on real-reader judgments instead, with this experiment as the cautionary tale.

## Background

We maintain a character-card authoring skill with an "anti-AI-flavor" discipline. In July 2026 a real user read one of our published cards — which scored 91.2/100 on our structural rubric — and called it out as obviously AI-written, pointing at specific sentences ("quote-capping" a fact with a character's witty one-liner, antithesis flips like "she swears without swear words — only facts"). That episode raised the obvious question: can we automate that reader's judgment with an LLM judge? Before wiring any judge into our iteration loop, we ran a calibration.

## Design

- **Judges**: 4 heterogeneous base models in rounds 1–2 (gemini-3-pro, claude-sonnet-5, kimi-k2.6, deepseek-v4-pro; same-base multi-instance judges share priors, so we didn't use them), expanded to 10 judges across 6 base families in round 3.
- **Material**: pairs of card opening messages. Three layers:
  - **Gold anchors** (known answers): our v1 cards that a real reader had flagged as AI × top community cards by human authors (each with millions of chat interactions);
  - **Main trial**: our rewritten v1.1 cards × human cards;
  - **Self-control**: same card, v1 vs v1.1 (before/after the de-AI-flavor rewrite).
- **Blinding**: HTML stripped, all character names replaced with aliases (zero real-name leakage), passages truncated to comparable lengths.
- **Order control**: every pair judged in both AB and BA order (position bias turned out to be large: roughly 1 in 3 verdicts flipped with position).
- **Prompt variants**: round 1 taught judging criteria; round 2 was neutral (to rule out criteria leakage); round 3 added few-shot and explicit anti-bias variants.

## Results

| Metric | R1 (with criteria) | R2 (neutral) | Reading |
| --- | --- | --- | --- |
| Gold-anchor accuracy | **2/16 = 12%** | **2/16 = 12%** | Judges are systematically inverted, not merely noisy |
| Position stability | 68% | 63% | ~1/3 of verdicts flip with A/B position |
| Inter-judge agreement | 82% | 86% | High agreement — **consistently wrong** (consistency ≠ correctness) |
| Self-control (v1 vs v1.1) | 11:5 | 8:7 | The only weakly informative axis; shrinks to noise in R2 |

Round 3, per-judge gold accuracy (the table reports the anti-bias / few-shot configurations; the remaining judges ran the neutral prompt and stayed in the 12% band):

| Judge | Prompt | Gold accuracy |
| --- | --- | --- |
| claude-4.6-sonnet | anti-bias | **10/12 = 83%** |
| claude-4.6-sonnet | few-shot | 7/12 = 58% |
| deepseek-v4-pro | anti-bias | 4/12 = 33% |
| glm-5.2 | anti-bias | 4/12 = 33% |
| qwen3.7-max | anti-bias | 3/12 = 25% |
| (R1/R2 judges, neutral / with criteria) | — | 12% |

## Why the judges vote backwards

Reading the judges' stated reasons for the gold misjudgments, one pattern repeats: LLM judges treat **high detail density, inner-voice OS blocks, status-bar layouts, and tight information packing** as evidence of humanity. Our v1 cards were built by diligently imitating community card templates — "spec-perfect" texts — and the judges recognized the *fluency of format imitation*, not the *looseness of human texture*. Meanwhile, genuinely human-written popular cards read "cliché" (melodramatic romance beats, stock rich-guy props), and the judges equated **cliché with fake**. But cliché is exactly the dominant texture of a real creator community. One judge cited a status-bar layout as proof of a "real community author's interaction design" — that layout was the part our generator imitated most faithfully.

Removing the criteria coaching in R2 changed the judges' vocabulary but not their verdicts, so this is not a prompt artifact. It looks like a shared base-model prior: **the LLM's concept of "human-sounding" is systematically misaligned with what human readers actually perceive.**

## What we do instead

1. **Rule-based detector as the regression gate** (`tools/score_card.py`, `ai_flavor_scan`): deterministic, explainable, calibrated on the sentences real readers actually flagged. Quota-based sentence-pattern checks (quote-capping ≤1, antithesis flips ≤1, invented concept words =0, fake-precision numbers ≤3 …) rather than word blacklists. During calibration the first gold run caught the detector itself over-firing on human prose (bare "一丝/一抹" scenery phrases); we down-weighted isolated stock words and kept sentence-pattern hits heavy — after the fix, human samples scored clean with zero false positives while AI drafts still got flagged. The public regression is in [`tools/tests/test_ai_flavor_gold.py`](../tools/tests/test_ai_flavor_gold.py).
2. **Real-reader spot checks as the only ground truth.** The detector gates regressions; it does not define quality.
3. **LLM blind review only for relative judgments** (same card, before vs after a rewrite), always in both AB/BA orders, and only with the one configuration that passed 80% gold (claude + anti-bias) — and even then, 83% is not gate-grade.

## Honest limits

- The gold "human" corpus is top community cards; we cannot redistribute them, so the public regression in this repo covers our own drafts and rewrites plus the reader-flagged sentences (the human-side zero-false-positive result is reported here but not reproducible from shipped data alone).
- The detector only catches **known sentence-level patterns**. Early generated cards that imitated templates cleanly pass it at full marks — their AI flavor lives in *evenly distributed detail density, zero idle details, perfect structure*, a whole-texture signal that neither our rules nor LLM judges catch. Full marks ≠ human-sounding. There is no automated silver bullet for 人味; human reading stays in the loop.
- Sample sizes are small (16 gold judgments per round in R1/R2, 12 in R3). We treat the numbers as strong directional evidence, not precise estimates.

## Related work

Position bias and self-preference in LLM judges are documented (e.g. MT-Bench, arXiv:2306.05685). What this experiment adds is a domain-specific gold-calibration failure mode: on "does this read human?" for roleplay prose, high judge agreement coexisted with inverted verdicts — a reminder that agreement metrics alone cannot validate a judge.

---

## 中文摘要

我们让多个 LLM 评委盲评「真人写的角色卡 vs AI 写的角色卡」：评委间一致率 86%，但在已知答案的 gold 锚点上正确率只有 **12%**——它们一致地把「被真实读者一眼判 AI」的卡评为更像真人、把百万对话量级的人类热门卡判成 AI。根因：LLM 把「细节密度高/有状态栏/句句工整」当人味证据，恰好是 AI 特征；把「俗」当「假」，而俗恰恰是真实社区的主流质感。显式反偏提示能把 claude 拉到 83%，对 deepseek/qwen/glm 几乎无效（硬先验）。结论：**没过 gold 校准（≥80%）的 LLM 评审器不得当优化目标**；我们改用「规则检测器（真实读者判例校准）当回归闸门 + 真人抽检当真值 + LLM 盲评仅做同卡改前改后的相对判断」。诚实边界：规则只抓得住已知句式病灶，「细节密度均匀/零闲笔/结构完美」的整体纹理层 AI 味，规则和 LLM 评委都抓不到——人味没有自动化银弹。
