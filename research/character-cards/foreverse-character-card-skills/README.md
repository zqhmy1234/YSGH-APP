# character-card-skills

Agent skills, 18 original character cards, and calibrated AI-flavor detectors for roleplay card authors — including, as far as we know, the first **English-native** card-authoring skill in the agent-skills ecosystem (calibrated on what English communities actually publish, not translated from another language's rules). Skills follow the open [SKILL.md convention](https://github.com/anthropics/skills) — they run in Claude Code, Cursor, Codex, Gemini CLI, and natively inside [Foreverse](https://foreverse.app). Cards import into SillyTavern (v2 JSON + v3 PNG included) and any chara_card_v2/v3 frontend.

[![cards gate](https://github.com/foreverse-app/character-card-skills/actions/workflows/score.yml/badge.svg)](.github/workflows/score.yml)
[![code: MIT](https://img.shields.io/badge/code-MIT-green.svg)](LICENSE)
[![content: CC BY 4.0](https://img.shields.io/badge/content-CC%20BY%204.0-blue.svg)](LICENSE-CONTENT)
[![chat: Discord](https://img.shields.io/badge/chat-Discord-5865F2.svg?logo=discord&logoColor=white)](https://foreverse.app/discord)

Why this repo exists, in one experiment: we asked LLM judges to tell human-written cards from AI-written ones, blind. They agreed with each other 86% of the time — and were right **12%** of the time, rating the *human* cards as AI and the AI cards as human. LLM judges take detail density, status-bar layouts and polished sentences as proof of humanity. Real readers take the same things as proof of a machine. So we stopped asking models whether prose sounds human, wrote down what real readers flagged, and turned it into a rule-based discipline plus a deterministic detector. Full writeup: [docs/blind-review-experiment.md](docs/blind-review-experiment.md) · [blog version](https://foreverse.app/blog/llm-judges-called-humans-ai) · [release post](https://foreverse.app/blog/open-sourcing-character-card-skills).

## What's inside

| Path | What | Language |
| --- | --- | --- |
| [`skills/character-card-author/`](skills/character-card-author/) | The Chinese-native authoring skill: four-axis positioning → 47 genre playbooks → five opening paradigms, nine liveliness techniques, lorebook patterns, output-rhythm laws, card-copy prose discipline. Plus 18 cover-art style guides and 7 annotated example cards | zh (EN mirror: [SKILL.en.md](skills/character-card-author/SKILL.en.md)) |
| [`skills/character-card-author-en/`](skills/character-card-author-en/) | The **English-native** authoring skill. Same method skeleton, every calibration re-derived from Western evidence: a 30-card sample of top-starred SFW community cards (greeting median 178.5 words, 0/30 rich HTML), the rentry guide canon, platform docs. Adds English-only disciplines: the no-user-actions red line, AnyPOV pronoun engineering, permanent-token budgets, a 95-phrase English slop lexicon, naming-slop bans | en |
| [`skills/chat-quality-doctor/`](skills/chat-quality-doctor/) | The triage skill for "I chatted and it feels off": symptom table → diagnosis → scoped surgery → verification, with 8 per-model RP symptom profiles and a slop lexicon | zh (EN mirror: [SKILL.en.md](skills/chat-quality-doctor/SKILL.en.md)) |
| [`skills/style-anchor-fusion/`](skills/style-anchor-fusion/) | Corpus-grounded style mimicry for long-form prose: pick 2–3 complementary source novels (skeleton / atmosphere / prose ceiling), deep-read them into a "voice-anchor notes" file, then write with the notes (what to sound like) **plus** the anti-AI-flavor discipline (what to ban). Evidence framing after a human re-audit: machine-verified gains only — discipline drives slop to 0.00, corpus contributes at the vocabulary/objects/register layer, while corpus-only remains a net negative; the earlier "ranked #1 in blind review" claim is downgraded to LLM-judge preference (the judges rewarded a fragment cadence that measurably points *away* from human-written books). Adds fragment-cadence guardrails + acceptance discipline. Ships an anti-plagiarism sliding-window gate (`tools/plagiarism_check.py`) and a fragment-cadence census (`tools/fragment_census.py`) | zh (EN mirror: [SKILL.en.md](skills/style-anchor-fusion/SKILL.en.md)) |
| [`cards/zh/`](cards/zh/) · [`cards/en/`](cards/en/) | 12 Chinese + 6 English original cards, every one shipped as `card.md` source + `*.v2.json` + PNG-embedded card + cover | zh + en |
| [`tools/`](tools/) | `score_card.py` (8-dimension scorer, zh calibration) · `score_card_en.py` (8-dimension scorer, English calibration, with red-line auto-fails) · `ai_flavor_check.py` (standalone flavor scan) · `card_md_to_st.py` (md → v2 JSON + v3 PNG, zero deps) · `blind_review/` (the judge harness) | Python 3.9+, no third-party deps |
| [`docs/`](docs/) | [The experiment](docs/blind-review-experiment.md) · [before/after surgery record](docs/before-after.md) · [full matrices](docs/matrix.md) · [card.md format spec](docs/card-md-format.md) | en + zh |

## Use the cards

Grab any PNG under `cards/` and import it — SillyTavern (character import), Foreverse (character page → import), or anything that reads `chara`/`ccv3` PNG chunks. The `.v2.json` next to it is the same card for frontends that prefer JSON. Lorebooks, alternate greetings and example dialogues are embedded.

## Running SillyTavern cards on Android

Every card in this repo runs on a phone; the question is where the server lives. The three real options in 2026: SillyTavern inside Termux (free, but Android's phantom-process killer hunts the Node server), a cloud VPS you administer yourself, or a native tavern app with no server at all. We compared the three honestly — exact commands, failure modes, who should pick which — in [SillyTavern on Android: three roads](https://foreverse.app/blog/sillytavern-on-android-three-ways). The native-app road is what [Foreverse on Google Play](https://play.google.com/store/apps/details?id=app.foreverse.android) is for: PNG/JSON/charx cards, lorebooks, presets, regex and group chats import directly, no Termux and no server, BYOK across 62 providers.

## Use the skills

```bash
git clone https://github.com/foreverse-app/character-card-skills
# Claude Code
cp -r character-card-skills/skills/* ~/.claude/skills/
# Cursor (project-level)
cp -r character-card-skills/skills/* your-project/.cursor/skills/
# Codex
cp -r character-card-skills/skills/* ~/.codex/skills/
```

Then ask your agent to "write a character card" (写一张角色卡) — it will route through the four-axis positioning, read the matching genre playbook, and self-check against the prose discipline. For English-audience cards, ask for "an English character card" and the agent should read `character-card-author-en` instead. In the [Foreverse Android app](https://foreverse.app) the same skills are built in, wired to native scoring, cover generation, one-tap import and community publishing.

Two authoring skills, two calibrations — not a translation pair. The Chinese skill's rules come from Chinese community data (its `SKILL.en.md` is a translated mirror for agents that prefer English instructions). The English skill's rules come from English community data, and several of them *contradict* the Chinese ones where the communities genuinely differ: greeting bands (150–350 words vs 800–2,600 characters), HTML status panels (a Western anti-pattern vs a Chinese art form), lorebook density, even the em-dash (an AI tell in Chinese prose; ordinary furniture in English RP prose — our calibration data showed 8 of 30 top human English cards exceed the density that would flag a Chinese card). The first three English cards in `cards/en/` (Rosalind, Caleb, The Lighthouse) were written *before* that re-derivation, with the Chinese methodology on an English surface; they stay in the repo as an honest contrast group. The three newer ones (Edmund, Frankie, Redline) were written by the English-native skill.

## Run the tools

```bash
python3 tools/score_card.py cards/zh/*/card.md        # 8-dimension gate (zh), <75 → exit 1
python3 tools/score_card_en.py cards/en/frankie-doyle/card.md   # 8-dimension gate (en) + red lines
python3 tools/ai_flavor_check.py some-prose.txt        # standalone flavor scan
python3 tools/plagiarism_check.py --corpus source-novel.txt \
    --target draft-dir/                                # style-mimicry anti-plagiarism gate (15-char window)
python3 tools/fragment_census.py --target draft-dir/   # fragment-cadence census vs human-book band (zh prose)
python3 tools/card_md_to_st.py cards/zh/peiyu/card.md --slug peiyu \
    --avatar cards/zh/peiyu/cover.png --outdir out/    # md → v2 JSON + v3 PNG
python3 tools/tests/test_ai_flavor_gold.py             # detector gold regression
```

The English scorer adds five red lines that fail a card regardless of score: user actions/dialogue written into the greeting, NSFW lexicon on an SFW card, naming-slop character names, clinical/therapy claims, and a permanent-token estimate over 2,000.

CI scores every Chinese card with the zh gate and the three English-native cards with the en gate (≥75 + red lines), runs the detector's gold regression, and round-trips every card through the converter on each push.

## The discipline, in one before/after

A card of ours scored 91.2/100 on structure. A real reader called it AI on sight and named the sentences. That gap became the "card-copy prose discipline" (seven quota-checked flaw patterns). One pair from the surgery record:

> **Before** (AI-flavored): 大二摔栏摔的，她说那是「学费」。 — *hurdling injury from sophomore year; "tuition," she calls it.* Every fact gets capped with a witty quote. Uniform cleverness is the fingerprint.
>
> **After**: 大二跨栏的时候摔过一次，锁骨下面留了道浅疤。 — *fell hurdling in sophomore year; a faint scar under the collarbone.* Facts laid flat. The wit budget is one per card.

The full seven-pattern record with detector evidence: [docs/before-after.md](docs/before-after.md). The counterintuitive part: fixing AI flavor is mostly a job of deletion — cut the cleverness, keep the idle details. The detector had to be de-tuned the same way (isolated stock phrases like 一丝凉意 are human; only pattern-level hits count). Detection philosophy and its honest limits: [docs/blind-review-experiment.md](docs/blind-review-experiment.md).

## Coverage

47 genre playbooks (15 female-oriented / 9 male-oriented / 18 universal / 5 form-layer), each with audience analysis, archetype library, opening paradigms, a 30-turn pacing table, lorebook templates, cover-art recipe and a genre-specific 10+ item checklist. 18 cover-art style guides. 8 per-model symptom profiles with confidence labels (`observed` / `community` / `hypothesis` / `stale` — stale profiles are barred from quoting exact numbers, a rule the doctor skill enforces on itself). Full tables: [docs/matrix.md](docs/matrix.md).

## Card gallery

![card covers](docs/assets/gallery.jpg)

| Card | Genre | Opening paradigm | Score |
| --- | --- | --- | --- |
| 宋知夏 Song Zhixia | campus / sprinter senior, injured & lying about it | event-scene | 88.9 |
| 江迟野 Jiang Chiye | campus / delinquent with a sketchbook | event-scene | 91.8 |
| 霍聿深 Huo Yushen | office / boss with a locked drawer | event-scene | 92.6 |
| 奈芙尔 Nefer | fantasy / cursed cat-eared alchemist | event-scene | 92.3 |
| 苏念栀 Su Nianzhi | campus / system-quest heroine | event-scene | 91.3 |
| 裴聿 Pei Yu | 古风 / Tang-dynasty magistrate, serial case | event-scene | 93.9 |
| 沈砚 Shen Yan | urban / art-money revenge patron | event-scene | 92.0 |
| 纪闻笙 Ji Wensheng | urban / composer fiancé at 3 a.m. | conflict-drop | 86.2 |
| 唐团团 Tang Tuantuan | slice-of-life / bakery owner at closing time | event-scene | 89.9 |
| 玄净 Xuan Jing | 仙侠 / sword master, three-year discipleship | event-scene | 90.0 |
| 聂小倩 Nie Xiaoqian | 聊斋同人 (public-domain) / ghost at the temple | event-scene | 88.4 |
| 晚孤市 Wangu Market | simulator / dream night-market, multi-NPC | guide-console | 92.7 |
| Edmund Harrower | fantasy / rival chartmakers, one boat, six weeks | event-scene + confrontation | 94.0 (en) |
| Frankie Doyle | modern cozy / 3 a.m. diner, grumpy owner | event-scene | 94.0 (en) |
| Redline Salvage Co. | sci-fi / salvage tug crew, debt and dead ships | world-broadcast | 94.0 (en) |
| Rosalind Vane | fencing instructor, comeback denied | event-scene | contrast group |
| Caleb Moore | garage apprentice / midnight radio host | dual-POV | contrast group |
| The Lighthouse at Grey Hollow | 1897 keeper station, rule-horror world card | world-broadcast | contrast group |

Chinese scores are from `tools/score_card.py` (calibrated on Chinese community data); English scores are from `tools/score_card_en.py` (calibrated on Western community data — the two numbers are not comparable to each other). The three contrast-group cards predate the English-native skill and deliberately keep their original form (Chinese methodology, English surface: long greetings, panel instincts, numeric affection ladders); scored today by the en gate they land at 74–85, and the *shape* of what they lose points on is the whole lesson. Every card passes the anti-AI-flavor full-text scan; English slop control for the new cards additionally runs through the en scorer's 95-phrase lexicon and pattern quotas.

Content note: the genre playbooks cover adult-genre *narrative methodology* (dark-thriller, power-exchange and similar categories carry their own compliance clauses); the shipped cards themselves are SFW — tension on the page, nothing explicit.

## About Foreverse

We build [Foreverse](https://foreverse.app), a free Android app where novel reading, plot branching and character chat share one bookshelf — import a TXT/EPUB, fork the plot from any paragraph, chat spoiler-free with the cast, or hand a whole book to the on-shelf agent and get character cards drafted from it. Deep SillyTavern compatibility (PNG/JSON/charx, lorebooks, presets, regex, group chat) and BYOK for 60+ providers. The app itself is a free closed-source product; this repo is the open, standalone part and works without it. These skills started as the app's built-in agent skills; open-sourcing them is our way of paying the card-making community back — the statistical baselines came from reading what that community publishes. [Google Play](https://play.google.com/store/apps/details?id=app.foreverse.android) · [foreverse.app](https://foreverse.app) · [@foreverseapp](https://x.com/foreverseapp)

## Contributing & license

Issues and PRs welcome — cards must pass `tools/score_card.py` (≥75, anti-AI-flavor 5/5) and the genre checklist of their category; the CI gate enforces the first half automatically. Code is [MIT](LICENSE); skills, cards and docs are [CC BY 4.0](LICENSE-CONTENT) (attribute to this repo or foreverse.app). All cards are original; all cover art is AI-generated by us and labeled as such in each card's creator notes.

中文说明见 [README.zh.md](README.zh.md)。
