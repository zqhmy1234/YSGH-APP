#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""English character card structural scorer v1 (character-card-author-en).

Sister of score_card.py (the Chinese-calibrated scorer in this repo). Same
eight-dimension skeleton, all rules recalibrated for English/Western conventions:

  - Greeting band: 150-350 words full credit (30-card sample of top-starred SFW
    cards on a major community card hub, 2026-07: median 179w, p25=123, p75=219)
    instead of zh 800-2600 chars.
  - No-HTML discipline (0/30 sampled Western cards use HTML) — HTML is penalized
    except a light bracketed status line for RPG cards.
  - Permanent-token budget check (target 800-1500, red line 2000) — JanitorAI
    official guides + pixi + live-card observation.
  - English slop lexicon (Sukino Banned Tokens + Antislop paper + EQ-Bench) with
    cluster-density scoring; "not X, but Y" tracked separately.
  - RED LINES (auto FAIL regardless of score): user-action/dialogue in greeting,
    NSFW lexicon (nsfw_level=0 line), naming slop as char name, >2000 permanent
    tokens, therapy claims.

Dimensions (100): fields 13 / greeting 18 / liveness9 24 / tags 10 /
                  format&replay 12 / lorebook 8 / image prompt 10 / anti-slop 5

Usage:
    python3 score_card_en.py card1.md [card2.md ...] [--json]
Exit code 1 if any card scores <75 or hits a red line.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any

SECTION_RE = re.compile(r"^##\s+(.+?)\s*$", re.M)

# ---------- tag vocabularies (Western community search language) ----------
AUDIENCE_TAGS = {"fem-audience", "male-audience", "all-audience", "female-oriented", "male-oriented"}
POV_TAGS = {"anypov", "fempov", "malepov"}
GENRE_TAGS = {"fantasy", "modern", "campus", "college", "sci-fi", "scifi", "historical",
              "supernatural", "urban", "post-apocalyptic", "space", "cozy", "horror-lite",
              "mystery", "slice-of-life", "royalty-au", "workplace", "sports"}
TROPE_TAGS = {"enemies-to-lovers", "slow-burn", "hurt-comfort", "hurt/comfort", "found-family",
              "grumpy-x-sunshine", "forced-proximity", "fake-dating", "friends-to-lovers",
              "second-chance", "royalty", "arranged-marriage", "rivals", "mentor",
              "age-gap", "ice-queen", "hard-to-win", "yandere-lite", "tsundere",
              "morally-gray", "bodyguard", "childhood-friends", "cozy", "comfort",
              "adventure", "survival", "heist", "detective", "slowburn"}
MODE_TAGS = {"single-character", "scenario", "rpg", "simulator", "multi-npc", "narrator", "helper"}

# ---------- English slop lexicon (SFW groups A-H) ----------
# Sources, cross-checked: Sukino's Banned Tokens list (HuggingFace), the Antislop
# paper (arXiv 2510.15061), EQ-Bench slop score components, community complaint
# threads. Judged by cluster density, not single hits.
SLOP_PHRASES = [
    # A body reactions
    "shiver down", "shivers down", "sending shivers", "sent a shiver", "swallowed hard",
    "swallows hard", "breath hitch", "heart hammer", "heart pounded", "knuckles turning white",
    "knuckles whiten", "knuckles white", "adam's apple bob", "felt a twinge", "felt a chill",
    "stomach does a flip", "took a deep breath", "takes a deep breath", "felt a strange sense",
    "a breath he didn't know", "a breath she didn't know",
    # B voice
    "barely above a whisper", "barely a whisper", "voice barely audible", "husky voice",
    "husky whisper", "low purr", "chuckles darkly", "chuckled darkly", "voice thick with",
    "voice steady despite", "lacks any real bite", "grins wickedly",
    # C eyes/expression
    "eyes sparkling with", "eyes sparkle with", "eyes gleam", "eyes glint", "eyes never leaving",
    "eyes never left", "half-lidded", "didn't reach her eyes", "didn't reach his eyes",
    "knowing smile", "smirk playing on", "playfully smirk", "calculating gaze", "assessing look",
    "eyes darkened", "eyes widened",
    # D touch/motion
    "ministrations", "tracing a finger", "traced a finger", "practiced ease",
    "strand of hair behind", "tucking a strand", "tucked a strand", "fidget with the hem",
    "grips like a vice", "waggles her eyebrows", "towers over",
    # E constructions (regex handled separately for not-x-but-y)
    "a mix of", "with a mix of", "couldn't help but", "could not help but", "testament to",
    "despite himself", "despite herself", "despite themselves", "torn between",
    "maybe, just maybe", "little did she know", "little did he know", "little did they know",
    "for what felt like", "what seemed like an eternity", "unbeknownst",
    "whether you like it or not", "without waiting for a response",
    # F nouns/similes
    "tapestry", "symphony of", "kaleidoscope", "cacophony", "siren call", "siren's call",
    "moth to a flame", "predator stalking", "a dance as old as time", "soothing balm",
    # G atmosphere
    "dimly lit", "casting long shadows", "air is thick", "air was thick", "air hung thick",
    "air crackle", "dipped below the horizon", "dust motes", "hung in the air",
    "hung heavy in the air", "atmosphere was charged",
    # H endings/outlook
    "face it together", "was only just beginning", "only just getting started",
    "the night is still young", "for now, that was enough", "whatever lay ahead",
    "renewed sense of", "newfound sense of", "ball is in your court",
]
# NSFW group I — red line on nsfw_level=0 line (auto FAIL)
NSFW_PHRASES = [
    "wet heat", "slick folds", "sensitive flesh", "waves of arousal", "pebbled",
    "audible pop", "wet pop", "kiss-bruised", "bruising kiss", "make me yours",
    "claim me", "unless you want me to", "arousal pooling", "heaving with desire",
    "pupils blown", "loins",
]
# naming slop (Antislop paper: model-fixated names; Elara 85,513x over human baseline)
SLOP_NAMES = {"elara", "kael", "seraphina", "lyra", "aria", "althea", "elias voss", "sarah chen"}

SLOP_REGEX = [
    (r"\bnot (?:just )?(?:a |an |the )?\w+(?:\s\w+){0,3}, but\b", '"not X, but Y" construction'),
    (r"\bit(?:'s| is| was)n't (?:just )?\w+(?:\s\w+){0,2}\. it(?:'s| is| was)\b", '"It wasn\'t X. It was Y." construction'),
    (r"\bwhat (?:do|will) you (?:say|do)\b", "fishing-line ending"),
    (r"\bthe choice is yours\b", "fishing-line ending (the choice is yours)"),
]

# user-action red line: narration (outside quotes) where you/{{user}} is subject of an action verb
USER_ACTION_VERBS = (
    r"say|says|said|reply|replied|walk|walked|take|took|grab|grabbed|pick|picked|nod|nodded|"
    r"smile|smiled|laugh|laughed|whisper|whispered|shout|shouted|step|stepped|turn|turned|"
    r"reach|reached|hand|handed|open|opened|sit|sat|stand|stood|run|ran|follow|followed|"
    r"decide|decided|agree|agreed|answer|answered|speak|spoke|move|moved|pull|pulled|push|pushed"
)
USER_ACTION_RE = re.compile(rf"(?:^|[.!?]\s+|\*\s*)(?:you|\{{\{{user\}}\}})\s+(?:{USER_ACTION_VERBS})\b", re.I)
# user-mind: penalty (not auto-fail) — gamebook tradition tolerates limited use
USER_MIND_RE = re.compile(r"\b(?:you|\{\{user\}\})\s+(?:feel|felt|think|thought|know|knew|realize|realized|remember|remembered|understand|understood|can't help|cannot help|wonder|wondered)\b", re.I)

# Therapy red line scans narrative surfaces only (greeting/examples/description/creator
# notes) — prohibition language in system_prompt ("never diagnose") is the OPPOSITE of a
# clinical claim (same lesson as zh scorer: ban-lists must not count as hits). Negated
# mentions ("never/not/no ... diagnose") are skipped wherever they appear.
THERAPY_RE = re.compile(r"(?<!never )(?<!not )(?<!don't )(?<!no )\b(?:your therapist|licensed (?:therapist|psychologist)|therapy session|professional counsel|i can diagnose|diagnoses you)\b", re.I)


def parse_card(path: Path) -> dict[str, Any]:
    text = path.read_text("utf-8")
    front: dict[str, str] = {}
    body = text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            raw = text[3:end].strip()
            body = text[end + 4:]
            key = None
            for line in raw.splitlines():
                if re.match(r"^\s", line) and key:
                    front[key] += "\n" + line.strip()
                    continue
                if line.lstrip().startswith("#"):
                    continue
                if ":" not in line:
                    continue
                key, val = line.split(":", 1)
                key = key.strip()
                front[key] = val.strip().strip("'\"")
    sections: dict[str, str] = {}
    matches = list(SECTION_RE.finditer(body))
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        sections[m.group(1).strip().lower()] = body[start:end].strip()
    return {"path": str(path), "text": text, "front": front, "sections": sections}


def sec(card: dict[str, Any], *names: str) -> str:
    out = []
    for n in names:
        v = card["front"].get(n) or card["front"].get(n.lower()) or ""
        if v:
            out.append(str(v))
        v2 = card["sections"].get(n.lower())
        if v2:
            out.append(v2)
    return "\n".join(out)


def split_tags(raw: str) -> list[str]:
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        raw = raw[1:-1]
    return [x.strip().strip("'\"").lower() for x in re.split(r"[,，|/]", raw) if x.strip()]


def strip_html(s: str) -> str:
    s = re.sub(r"<style[\s\S]*?</style>", "", s, flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    return s


def strip_quotes(s: str) -> str:
    """Remove quoted dialogue spans so narration-only scans don't hit character speech."""
    s = re.sub(r"[\"“][^\"”]{2,}[\"”]", " ", s)
    return s


def words(s: str) -> int:
    return len(re.findall(r"[A-Za-z0-9''-]+", s))


def est_tokens(s: str) -> int:
    return int(words(s) * 1.33)


def clamp(v: float, hi: float) -> float:
    return max(0.0, min(hi, v))


def score_card(card: dict[str, Any]) -> dict[str, Any]:
    text = card["text"]
    front = card["front"]
    tags = split_tags(str(front.get("tags", "")))
    tagset = set(tags)
    first = sec(card, "first message", "first_mes", "greeting")
    first_prose = strip_html(first)
    desc = sec(card, "description")
    personality = sec(card, "personality")
    scenario_f = str(front.get("scenario", ""))
    scenario_s = card["sections"].get("scenario", "")
    scenario = scenario_f + "\n" + scenario_s
    examples = sec(card, "example dialogue", "mes_example")
    system = str(front.get("system_prompt", ""))
    creator = sec(card, "creator notes", "creator_notes")
    image_prompt = str(front.get("image_prompt", ""))
    lore_body = card["sections"].get("lorebook", "")
    alt_greets = re.findall(r"^##\s+alternate greeting", text, re.M | re.I)
    detail: dict[str, Any] = {}
    red_lines: list[str] = []

    # ---------- permanent budget ----------
    permanent = est_tokens(desc + " " + personality + " " + scenario)
    detail["permanent_tokens_est"] = permanent
    if permanent > 2000:
        red_lines.append(f"permanent tokens ~{permanent} > 2000 (bloat red line)")

    # ============ A. fields (13) ============
    required = {
        "name": bool(front.get("name")),
        "desc>=120w": words(desc) >= 120,
        "personality>=60w": words(personality) >= 60,
        "scenario>=12w": words(scenario) >= 12,
        "first_mes>=80w": words(first_prose) >= 80,
        "system_prompt>=100w": words(system) >= 100,
        "creator_notes>=50w": words(creator) >= 50,
        "image_prompt": len(image_prompt) >= 80,
    }
    completeness = clamp(sum(required.values()) / len(required) * 12 + (1 if alt_greets else 0), 13)
    detail["required"] = {k: bool(v) for k, v in required.items()}
    detail["required"]["alt_greeting(bonus)"] = bool(alt_greets)

    # ============ B. greeting structure (18) ============
    n = words(first_prose)
    is_rpg = bool(tagset & {"rpg", "scenario", "simulator", "narrator"})
    hi_band = 550 if is_rpg else 350
    if 150 <= n <= hi_band:
        len_pts = 5.0
    elif 80 <= n < 150 or hi_band < n <= 600:
        len_pts = 3.0
    elif n > 0:
        len_pts = 1.0
    else:
        len_pts = 0.0
    if n > 600:
        len_pts = 0.5  # bloat
    has_scene = bool(re.search(r"\b(?:midnight|dawn|dusk|morning|evening|afternoon|tonight|am\b|pm\b|\d{1,2}:\d{2}|monday|tuesday|wednesday|thursday|friday|saturday|sunday|winter|summer|autumn|spring|rain|snow|street|bar|shop|club|deck|dock|harbor|harbour|office|kitchen|library|tavern|castle|station|diner|room|hall|market|road)\b", first_prose[:900], re.I))
    has_event = bool(re.search(r"\b(?:just|suddenly|mid-|halfway|interrupt|drops?|slams?|crashes?|rings?|buzzes?|snaps?|spills?|breaks?|storms?|arrives?|bursts?|freezes?|stops? dead|catches? you|clocks? you|looks? up)\b", first_prose, re.I))
    has_dialogue = bool(re.search(r"[\"“][^\"”]{2,}[\"”]", first))
    has_hook = bool(re.search(r"\b(?:you|\{\{user\}\}|your)\b", first_prose, re.I))
    quad_pts = sum([has_scene, has_event, has_dialogue, has_hook]) * 2.0
    # private life evidence + open ending
    has_private = bool(re.search(r"\b(?:text|message|missed call|group chat|voicemail|sister|brother|mother|regular|neighbor|neighbour|coworker|colleague|crew|customer|landlord|roommate|friend)\b", first_prose, re.I))
    tail = first_prose.rstrip()[-160:]
    bad_ending = bool(re.search(r"(?:waits? for your (?:answer|reply|response)|what will you do|what do you say|the choice is yours|up to you now)\s*[.?!]*\s*$", tail, re.I))
    comp_pts = (2.0 if has_private else 0.0) + (3.0 if not bad_ending else 0.0)
    greeting = clamp(len_pts + quad_pts + comp_pts, 18)
    detail["greeting"] = {"words": n, "scene": has_scene, "ongoing_event": has_event,
                          "char_dialogue": has_dialogue, "user_hook": has_hook,
                          "private_life": has_private, "bad_ending": bad_ending}

    # greeting user-discipline red line (scan narration only)
    narration_first = strip_quotes(first_prose)
    ua = USER_ACTION_RE.findall(narration_first)
    if ua:
        red_lines.append(f"greeting writes {{user}} actions/dialogue: {ua[:3]}")
    um = USER_MIND_RE.findall(narration_first)
    detail["greeting"]["user_mind_hits"] = len(um)

    # ============ C. liveness 9 (24) ============
    whole = "\n".join([first_prose, personality, examples, desc, system])
    w = {}
    w["micro_flaws"] = bool(re.search(r"\b(?:left on read|doesn't reply|ignores? the|hangs? up|drafts? and deletes?|half-finished|puts? off|procrastinat|shrugs? it off|pretends? not to|talks? over|forgets? names|late again|chews? (?:a|her|his) pen|bites? (?:a|her|his) nail|mutters|under (?:her|his) breath|doesn't look up|keeps? (?:wiping|counting|stacking)|re-inks?|straightens?|squares? the (?:edges?|chart)|rearranges?|checks? (?:it |the lock )?twice|eats? standing|answers? the previous question|deals? cards to nobody|superstiti|not turning around|without being asked|out of habit)\b", whole, re.I))
    w["say_do_gap"] = bool(re.search(r"\b(?:says? (?:one thing|otherwise)|claims?|insists?|denies|swears?)\b[^.!?]{0,80}\b(?:but|yet|while|though)\b|contradict|(?:will|would) deny|habit (?:she|he|they)(?:'d| would) deny|refuses? to (?:name|admit)|both pretend|pretends? (?:neither|not to)|loudly refuses|insists?[^.!?]{0,70}(?:anyway|keeps?|regardless)|,\s*denied\b|(?:mouth|words?) says?[^.!?]{0,40}(?:hands?|feet|actions?)|say one thing[^.!?]{0,30}do", whole, re.I))
    w["refusal"] = bool(re.search(r"\b(?:refus(?:es?|als?)|declines?|\"no\.\"|not (?:tonight|today|happening)|walks? (?:away|out)|shuts? (?:it|that|the door)|off[- ]limits|won't (?:answer|say|talk|hold for)|drops? the subject|(?:subject|topic) is closed|door clos(?:es|ing)|charges? (?:for|extra)|bills?)\b", whole, re.I))
    w["private_life"] = bool(re.search(r"\b(?:sister|brother|mother|father|aunt|uncle|roommate|coworker|colleague|regulars?|neighbors?|neighbours?|group chat|crew|old friend|ex[- ]|landlord|customers?|club|team|shift|union|church|book club)\b", whole, re.I))
    w["verbal_fingerprint"] = bool(re.search(r"\b(?:verbal tic|catchphrase|says? [\"“]|fragments?|clipped|drawl|accent|slang|never says?|always says?|counts? in|swears? in|calls? (?:everyone|people)|pet names?|luv|hon|kiddo|sunshine|mate\b|aye\b|innit)\b", whole, re.I))
    w["physical_anchor"] = bool(re.search(r"\b(?:\d{2,4}|[A-Z][a-z]+ (?:Street|Road|Ave|Lane|Row|Bridge|Park|Station|Bay|Harbor|Harbour)|Saab|Toyota|Ford|Nokia|Zippo|Leon Paul|Thermos|IKEA|Walmart|Tesco|7-Eleven|diner|brand)\b", whole))
    w["unfinished_business"] = bool(re.search(r"\b(?:hasn't (?:told|paid|sent|opened)|still (?:owes?|hasn't|unread)|unanswered|unpaid|overdue|deadline|due (?:friday|tomorrow|next)|debt|unfinished|unsent|unopened|waiting on|pending|never past page|saving (?:up )?to|rent creep|fund in a|has n[o']t? (?:watched|deleted|rung|answered)|never watched)\b", whole, re.I))
    w["misreading"] = bool(re.search(r"\b(?:misreads?|misread|guess(?:es|ed)? wrong|guessed wrong|assumes?|jumps? to conclusions?|gets? it wrong|mistakes? (?:you|\{\{user\}\})|can only (?:see|go on)|doesn't know why|reads? (?:it|that|people) wrong|wrong idea|thinking they got)\b", whole, re.I))
    w["emotional_inertia"] = bool(re.search(r"\b(?:still (?:angry|rattled|sore|raw|short)|carries? (?:over|into)|carr(?:y|ies) moods? forward|(?:night|day|mood|weather) (?:carries|sits|lingers)|their own weather|from (?:the|this) morning|hasn't shaken|residual|takes? a while to|slow to (?:thaw|forgive|cool)|thaws? (?:is )?slow|a beat late|sits? in (?:him|her|them|you)|habit (?:precedes|comes before|before speech)|gets? louder for a while|adjust slowly|before (?:speaking|answering)[^.!?]{0,40}(?:first|always))\b", whole, re.I))
    liveness = clamp(sum(w.values()) / 9 * 21 + (3 if words(examples) >= 60 else 0), 24)
    detail["liveness9"] = w

    # ============ D. tags & positioning (10) ============
    has_aud = bool(tagset & AUDIENCE_TAGS)
    has_pov = bool(tagset & POV_TAGS)
    has_genre = bool(tagset & GENRE_TAGS)
    has_trope = bool(tagset & TROPE_TAGS)
    has_mode = bool(tagset & MODE_TAGS)
    audience = clamp((2.5 if has_aud else 0) + (2.5 if has_pov else 0) + (2 if has_genre else 0)
                     + (2 if has_trope else 0) + (1 if has_mode else 0), 10)
    detail["tags"] = {"audience": has_aud, "pov(必)": has_pov, "genre": has_genre,
                      "trope": has_trope, "mode": has_mode, "count": len(tags)}

    # ============ E. format discipline & replayability (12) ============
    has_html = bool(re.search(r"<(?:div|span|style|details|summary|table)\b", first, re.I))
    html_pts = 0.0 if has_html else 2.5           # Western cards: no HTML
    # tense consistency rough check: only dynamic narration verbs (statives like was/is
    # legitimately mix — e.g. backstory references inside a present-tense scene)
    narr = strip_quotes(first_prose)
    past_hits = len(re.findall(r"\b(?:walked|said|looked|turned|stood|grabbed|nodded|stepped|reached|pulled|crossed|settled|picked)\b", narr))
    pres_hits = len(re.findall(r"\b(?:walks|says|looks|turns|stands|grabs|nods|steps|reaches|pulls|crosses|settles|picks)\b", narr))
    tense_mixed = past_hits >= 3 and pres_hits >= 3
    tense_pts = 0.5 if tense_mixed else 2.0
    alt_pts = min(len(alt_greets), 3) * 1.5       # 2-4 alternates standard
    ex_refusal = bool(re.search(r"\{\{char\}\}:[^\n]*(?:\"don't|\"no\.|not tonight|drops? the subject|\"stop)", examples, re.I))
    ex_pts = (1.0 if words(examples) >= 60 else 0) + (1.0 if ex_refusal else 0)
    cn_firstline = bool(re.search(r"(?:first lines? that work|try opening|good opener|start with)", creator, re.I))
    cn_pts = 1.0 if cn_firstline else 0.0
    fmt = clamp(html_pts + tense_pts + alt_pts + ex_pts + cn_pts, 12)
    detail["format"] = {"no_html": not has_html, "tense_mixed": tense_mixed,
                        "alt_greetings": len(alt_greets), "example_words": words(examples),
                        "example_refusal_turn": ex_refusal, "creator_firstline_guide": cn_firstline}

    # ============ F. lorebook (8) ============
    lore_entries = re.findall(r"^###\s+(.+?)\s*$", lore_body, re.M) if lore_body else []
    lore = 0.0
    lore_detail: dict[str, Any] = {"entries": len(lore_entries)}
    if lore_entries:
        lore += 2.0
        lore += min(len(lore_entries), 4) * 0.5
        headers = lore_entries
        has_keyed = any("keys" in h for h in headers)
        has_const = any("constant" in h for h in headers)
        lore += 1.0 if has_keyed else 0
        lore += 1.0 if has_const else 0
        has_voice = any(re.search(r"voice|speech|dialogue samples?", h, re.I) for h in headers)
        has_director = bool(re.search(r"order:\s*9\d\d", lore_body)) or any(re.search(r"director|output|rhythm", h, re.I) for h in headers)
        has_npc = bool(re.search(r"\bNPC|friends?|family|crew|regulars?|staff|people\b", lore_body, re.I))
        has_vault = any(re.search(r"vault|secret|backstory|past|archive|cracks", h, re.I) for h in headers)
        lore += 0.75 if has_voice else 0
        lore += 0.75 if has_director else 0
        lore += 0.5 if has_npc else 0
        lore += 0.5 if has_vault else 0
        has_adv = any(re.search(r"(?:prob|secondary|sticky|cooldown|group)\s*:", h) for h in headers)
        lore += 0.5 if has_adv else 0
        # dead entry: keyed syntax missing keys and not constant
        dead = [h for h in headers if "keys" not in h and "constant" not in h]
        lore -= len(dead) * 1.0
        entry_bodies = re.split(r"^###\s+.+$", lore_body, flags=re.M)[1:]
        huge = sum(1 for b in entry_bodies if words(b) > 1400)
        lore -= huge * 1.0
        lore_detail.update({"keyed": has_keyed, "constant": has_const, "voice_samples": has_voice,
                            "director": has_director, "npc": has_npc, "vault": has_vault,
                            "advanced_meta(bonus)": has_adv, "dead_entries": len(dead), "huge_entries": huge})
    lore_score = clamp(lore, 8)
    detail["lorebook"] = lore_detail

    # ============ G. image prompt (10) ============
    ip = image_prompt
    img = 0.0
    if ip:
        img += 1.5
        img += 1 if re.search(r"(?:9:16|vertical|portrait)", ip, re.I) else 0
        img += 1.5 if re.search(r"(?:photograph|photo texture|photorealistic|painterly|semi-real|impasto|cel.?shad|watercolor|watercolour|comic|ink|cinematic|concept art|oil paint|gothic|ethereal)", ip, re.I) else 0
        img += 1 if re.search(r"(?:face|close.?up|upper (?:half|third|body)|bust|portrait framing|eyes at)", ip, re.I) else 0
        img += 1 if re.search(r"(?:holding|hand|grips?|carr(?:y|ies)|under (?:one )?arm|clutch)", ip, re.I) else 0
        img += 1 if re.search(r"(?:muted|desaturat|low.?key|single (?:warm|accent)|accent col|one warm|amber|rim light|bokeh|moody)", ip, re.I) else 0
        img += 1.5 if re.search(r"no (?:text|letters?|logos?|watermarks?)", ip, re.I) else 0
        img += 0.5 if re.search(r"(?:in (?:her|his|their) (?:twenties|thirties|forties|fifties)|middle-aged|young adult)", ip, re.I) else 0
        # penalties: CJK calligraphy/seal (zh cover convention — culture mismatch), age digits
        if re.search(r"(?:书法|印章|calligraphy|seal stamp|hanzi|kanji title)", ip, re.I):
            img -= 2
        if re.search(r"\b(?:1[89]|2\d|3\d)[- ]year[- ]old\b", ip):
            img -= 1
        bad = re.findall(r"\b(?:masterpiece|best quality|8k|ultra.?detailed|highly detailed)\b", ip, re.I)
        img -= len(bad) * 1.5
    image_score = clamp(img, 10)

    # ============ H. anti-slop (5, deduction) ============
    narrative = first_prose + "\n" + examples
    low = narrative.lower()
    slop_hits = [p for p in SLOP_PHRASES if p in low]
    for pat, label in SLOP_REGEX:
        m = re.findall(pat, narrative, re.I)
        if m:
            slop_hits.append(f"{label} x{len(m)}")
    # em-dash density: house-style lint ONLY, not an AI signal. Calibration on a
    # 30-card human sample (top-starred SFW community cards) showed 8/30 human hot
    # cards exceed 3/1000 narration words — em-dash does NOT separate human from AI
    # in English RP prose (unlike zh, where the long dash is the #1 machine
    # fingerprint). House budget: 6/1000w triggers an issue, never a deduction/FAIL.
    narr_all = strip_quotes(narrative)
    emdash = narr_all.count("—") + narr_all.count("--")
    per_k = emdash / max(words(narr_all), 1) * 1000
    emdash_lint = per_k > 6
    if bad_ending:
        slop_hits.append("greeting fishing-line ending")
    flavor = clamp(5 - len(slop_hits) * 1.0 - max(0, len(um) - 1) * 0.5, 5)
    detail["slop_hits"] = slop_hits
    detail["emdash_per_1000w"] = round(per_k, 1)

    # ---------- red lines ----------
    nsfw_hits = [p for p in NSFW_PHRASES if p in (text.lower())]
    if nsfw_hits:
        red_lines.append(f"NSFW lexicon on nsfw_level=0 line: {nsfw_hits[:4]}")
    cname = str(front.get("name", "")).strip().lower()
    if cname.split()[0] if cname else "":
        if cname.split()[0] in SLOP_NAMES or cname in SLOP_NAMES:
            red_lines.append(f"naming slop character name: {front.get('name')}")
    narrative_surface = "\n".join([first_prose, examples, desc, creator])
    if THERAPY_RE.search(narrative_surface):
        red_lines.append("therapy/clinical claim language")

    total = round(completeness + greeting + liveness + audience + fmt + lore_score + image_score + flavor, 1)
    issues: list[str] = []
    for k, v in required.items():
        if not v:
            issues.append(f"[fields] missing {k}")
    for k, v in w.items():
        if not v:
            issues.append(f"[liveness] {k} not detected")
    if not has_pov:
        issues.append("[tags] POV tag missing (anypov/fempov/malepov required)")
    if not has_aud:
        issues.append("[tags] audience tag missing")
    if has_html:
        issues.append("[format] HTML in greeting (Western cards: plain prose/asterisk only)")
    if tense_mixed:
        issues.append("[format] narration mixes past and present tense")
    if not alt_greets:
        issues.append("[format] no alternate greetings (official house standard: 2-4)")
    if words(examples) < 60:
        issues.append("[format] example dialogue missing/thin (official house standard: 2-4 <START> groups)")
    if len(lore_entries) < 4:
        issues.append(f"[lorebook] only {len(lore_entries)} entries (official cards: 4-10)")
    for s in slop_hits:
        issues.append(f"[slop] {s}")
    if emdash_lint:
        issues.append(f"[style-lint] em-dash {per_k:.1f}/1000w over house budget 6 (not an AI signal; tidy anyway)")
    if permanent > 1500:
        issues.append(f"[budget] permanent ~{permanent} tokens (target 800-1500)")
    if len(um) > 1:
        issues.append(f"[greeting] {len(um)} user-mind phrasings (you feel/know/think) — keep <=1")

    return {
        "path": card["path"],
        "total": total,
        "red_lines": red_lines,
        "scores": {
            "fields(13)": round(completeness, 1),
            "greeting(18)": round(greeting, 1),
            "liveness9(24)": round(liveness, 1),
            "tags(10)": round(audience, 1),
            "format&replay(12)": round(fmt, 1),
            "lorebook(8)": round(lore_score, 1),
            "image_prompt(10)": round(image_score, 1),
            "anti_slop(5)": round(flavor, 1),
        },
        "detail": detail,
        "issues": issues,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("cards", nargs="+")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    fail = False
    for p in args.cards:
        card = parse_card(Path(p))
        res = score_card(card)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=1))
        else:
            print(f"\n=== {res['path']} ===")
            print(f"TOTAL: {res['total']} / 100" + ("  ** FAIL (red line) **" if res["red_lines"] else ""))
            for k, v in res["scores"].items():
                print(f"  {k:20} {v}")
            if res["red_lines"]:
                print("  RED LINES:")
                for r in res["red_lines"]:
                    print(f"    !! {r}")
            if res["issues"]:
                print("  issues:")
                for i in res["issues"]:
                    print(f"    - {i}")
            print(f"  permanent tokens est: {res['detail']['permanent_tokens_est']}")
        if res["total"] < 75 or res["red_lines"]:
            fail = True
    return 1 if fail else 0


if __name__ == "__main__":
    sys.exit(main())
