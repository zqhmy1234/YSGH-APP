"""LoCoMo retrieval evaluation (offline, deterministic).

The benchmark is a *measurement* harness, not a runtime dependency: it turns
dialogue turns into a lexical ranking and asks whether the gold evidence turn
appears in the top-k. It exists so retrieval changes have a real number attached,
not a feeling.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Sequence

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def dialogue_turns(conversation: dict) -> list[tuple[str, str]]:
    """Return (dia_id, text) in session order; ignores metadata keys."""
    out: list[tuple[str, str]] = []
    for key, value in conversation.items():
        if key.endswith("_date_time") or not isinstance(value, list):
            continue
        for turn in value:
            out.append((str(turn["dia_id"]), str(turn["text"])))
    return out


def bm25_rank(question: str, turns: Sequence[tuple[str, str]], k: int = 10) -> list[str]:
    docs = [tokenize(text) for _, text in turns]
    n = len(docs)
    if not n:
        return []
    df = Counter(word for doc in docs for word in set(doc))
    avg = sum(len(doc) for doc in docs) / n
    scores: list[tuple[float, str]] = []
    for (dia_id, _), doc in zip(turns, docs, strict=False):
        score = 0.0
        for word in tokenize(question):
            if word not in doc:
                continue
            idf = math.log(1 + (n - df[word] + 0.5) / (df[word] + 0.5))
            score += idf * 2.2 * 2.2 / (1.2 * (0.25 + 0.75 * len(doc) / avg) + 2.2)
        scores.append((score, dia_id))
    scores.sort(key=lambda item: (-item[0], item[1]))
    return [dia_id for _, dia_id in scores[:k]]


def evaluate(dataset: Iterable[dict], k: int = 10) -> dict[str, float]:
    hits = 0
    rr = 0.0
    total = 0
    for case in dataset:
        turns = dialogue_turns(case["conversation"])
        for qa in case.get("qa") or []:
            gold = set(qa.get("evidence") or [])
            if not gold:
                continue
            ranked = bm25_rank(qa["question"], turns, k=k)
            total += 1
            if gold & set(ranked):
                hits += 1
            rank = next((i + 1 for i, dia in enumerate(ranked) if dia in gold), 0)
            rr += 1.0 / rank if rank else 0.0
    return {
        "questions": float(total),
        f"recall@{k}": hits / total if total else 0.0,
        "mrr": rr / total if total else 0.0,
    }
