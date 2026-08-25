"""RAG 检索指标库（行业标准,适配本项目）

检索层指标（BEIR/TREC 惯例）:
  recall_at_k / hit_rate_at_k / precision_at_k / mrr / ndcg_at_k
混合检索消融: dense-only / sparse-only / RRF 对比增益
答案质量三指标（Wave2-F 2026-08-26，RAGAS 范式）:
  faithfulness（答案引用原文比例）/ relevancy（答案与查询相关）/
  context_precision（上下文排序质量）——LLM 无 key 时用 n-gram 代理，见各函数注释

用法:
  from research.rag_benchmark.metrics import recall_at_k, ndcg_at_k, evaluate_retrieval
  from research.rag_benchmark.metrics import faithfulness, relevancy, context_precision
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable


def _truncate(ranked: list, k: int) -> list:
    return ranked[:k]


def recall_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """Recall@k：Top-k 中命中的相关数 / 总相关数（k=0 或无数相关返回 0）"""
    if k <= 0 or not relevant:
        return 0.0
    hit = sum(1 for rid in _truncate(ranked, k) if rid in relevant)
    return hit / len(relevant)


def hit_rate_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """HitRate@k：Top-k 是否至少命中一条相关（二元，业界常用）"""
    if k <= 0:
        return 0.0
    return 1.0 if any(rid in relevant for rid in _truncate(ranked, k)) else 0.0


def precision_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """Precision@k：Top-k 中相关占比（k=0 返回 0）"""
    if k <= 0:
        return 0.0
    hit = sum(1 for rid in _truncate(ranked, k) if rid in relevant)
    return hit / k


def mrr(relevant: set[str], ranked: list[str]) -> float:
    """MRR：首个相关结果的倒数排名（无命中返回 0）"""
    for i, rid in enumerate(ranked, 1):
        if rid in relevant:
            return 1.0 / i
    return 0.0


def _dcg(relevant: set[str], ranked: list[str], k: int) -> float:
    """DCG@k：相关度按 1/rank 衰减（二元相关）"""
    dcg = 0.0
    for i, rid in enumerate(_truncate(ranked, k), 1):
        if rid in relevant:
            dcg += 1.0 / math.log2(i + 1)
    return dcg


def ndcg_at_k(relevant: set[str], ranked: list[str], k: int) -> float:
    """nDCG@k：DCG / IDCG（理想排序下最大 DCG；无相关返回 0）"""
    if k <= 0 or not relevant:
        return 0.0
    dcg = _dcg(relevant, ranked, k)
    ideal = sorted(relevant, key=lambda _x: 0)[:k]  # 理想排序 = 全部相关在前
    idcg = _dcg(relevant, ideal, k)
    return dcg / idcg if idcg > 0 else 0.0


def evaluate_retrieval(
    queries: Iterable[dict],
    ranker,  # 函数：query → [(id, score), ...]
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> dict:
    """批量评估：对每个查询跑 ranker,聚合全部检索指标

    queries: [{query, expected: [id...], ...}]；ranker: callable(query_str) → [(id, score)]
    返回 {recall@k, hit_rate@k, precision@k, mrr, ndcg@k, n_queries}
    """
    agg = {f"recall@{k}": 0.0 for k in ks}
    agg.update({f"hit_rate@{k}": 0.0 for k in ks})
    agg.update({f"precision@{k}": 0.0 for k in ks})
    agg["mrr"] = 0.0
    agg["ndcg@3"] = 0.0
    n = 0
    for q in queries:
        ranked = [rid for rid, _score in ranker(q["query"])]
        relevant = set(q.get("expected", []))
        for k in ks:
            agg[f"recall@{k}"] += recall_at_k(relevant, ranked, k)
            agg[f"hit_rate@{k}"] += hit_rate_at_k(relevant, ranked, k)
            agg[f"precision@{k}"] += precision_at_k(relevant, ranked, k)
        agg["mrr"] += mrr(relevant, ranked)
        agg["ndcg@3"] += ndcg_at_k(relevant, ranked, 3)
        n += 1
    if n:
        for key in agg:
            agg[key] = round(agg[key] / n, 4)
    agg["n_queries"] = n
    return agg


def evaluate_retrieval_explicit(
    queries: Iterable[dict],
    ranker,  # 函数：query → [(id, score), ...]
    ks: tuple[int, ...] = (1, 3, 5, 10),
) -> dict:
    """显式相关口径批量评估（2026-08-25 P0-B 新增，诊断指标，不进 M1 门禁）

    只统计带显式 expected id 的查询，相关集 = 显式 id（单条检索口径）。
    背景：recall@k 若用 label 全集做分母，Top-3 理论上限仅 3/15~3/20≈0.2，
    指标失真（审查报告第一节）。产品口径 = 找一条相关记录 → recall 分母 = 显式相关条数。
    返回 {recall@k, hit_rate@k, mrr, ndcg@3, n_queries}（n_queries=显式查询数）。
    """
    agg = {f"recall@{k}": 0.0 for k in ks}
    agg.update({f"hit_rate@{k}": 0.0 for k in ks})
    agg["mrr"] = 0.0
    agg["ndcg@3"] = 0.0
    n = 0
    for q in queries:
        expected = q.get("expected") or []
        if not expected:
            continue
        ranked = [rid for rid, _score in ranker(q["query"])]
        relevant = set(expected)
        for k in ks:
            agg[f"recall@{k}"] += recall_at_k(relevant, ranked, k)
            agg[f"hit_rate@{k}"] += hit_rate_at_k(relevant, ranked, k)
        agg["mrr"] += mrr(relevant, ranked)
        agg["ndcg@3"] += ndcg_at_k(relevant, ranked, 3)
        n += 1
    if n:
        for key in agg:
            agg[key] = round(agg[key] / n, 4)
    agg["n_queries"] = n
    return agg


# ---- 答案质量三指标（Wave2-F 2026-08-26，RAGAS 范式；LLM 无 key → n-gram 代理）----
# 背景（RAG评测体系 §6/拿key后推进计划）：faithfulness/relevancy/context precision
# 三指标要求 M2 验收前落地基线。正式口径（RAGAS）用 LLM judge 判 claim/子问题；
# 本项目无 key 阶段用确定性 n-gram 代理（可复现、零费用），key 就绪后可在
# evaluate_answer_quality 的 judge 参数处换 LLM 实现（契约不变）。

_CJK_RUN = re.compile(r"[\u4e00-\u9fff]+")
_LATIN_RUN = re.compile(r"[a-zA-Z0-9]+")
_SENT_SPLIT = re.compile(r"[。！？!?；;\n]+")


def _content_tokens(text: str) -> set[str]:
    """内容词元：CJK 连续段按二元组切分（免分词）+ 拉丁单词/数字。

    例："杭州西湖旅行" → {杭州, 州西, 西湖, 湖旅, 旅行}；"marathon 42km" → {marathon, 42km}。
    """
    out: set[str] = set()
    for run in _CJK_RUN.findall(text or ""):
        if len(run) == 1:
            out.add(run)
            continue
        out.update(run[i : i + 2] for i in range(len(run) - 1))
    out.update(_LATIN_RUN.findall((text or "").lower()))
    return out


def faithfulness(
    answer: str,
    sources: list[str],
    *,
    threshold: float = 0.5,
) -> float:
    """faithfulness——答案引用原文比例（句子级 grounded 判定，n-gram 代理）

    口径（RAGAS）：答案中能被检索上下文支撑的比例。实现：答案按句切分，
    每句内容词元在来源词元并集中的覆盖率 ≥ threshold（默认 0.5）→ 该句"有据"。
    faithfulness = 有据句数 / 有内容句数（0 句 → 0.0；answer 空 → 0.0）。
    上限 1.0；代理口径不判"编造但词面未重叠"的语义幻觉，key 就绪后换 LLM judge。
    """
    if not answer or not sources:
        return 0.0
    src_tokens: set[str] = set()
    for s in sources:
        src_tokens |= _content_tokens(s)
    if not src_tokens:
        return 0.0
    sentences = [s for s in _SENT_SPLIT.split(answer) if s.strip()]
    if not sentences:
        return 0.0
    grounded = 0.0
    for sent in sentences:
        toks = _content_tokens(sent)
        if not toks:
            continue
        covered = len(toks & src_tokens) / len(toks)
        if covered >= threshold:
            grounded += 1.0
    return round(grounded / len(sentences), 4)


def relevancy(query: str, answer: str, *, embedder=None) -> float:
    """relevancy——答案与查询相关度（n-gram 覆盖代理；embedder 可叠加余弦）

    口径（RAGAS）：答案是否包含回答查询所需信息。代理：查询内容词元被答案
    覆盖的比例（recall），span 0..1。embedder 提供时（callable(query/answer) → vec），
    与余弦各取 0.5 加权（key/模型就绪后启用；无 embedder 纯 n-gram）。
    query/answer 空 → 0.0。
    """
    if not query or not answer:
        return 0.0
    q_toks = _content_tokens(query)
    if not q_toks:
        return 0.0
    a_toks = _content_tokens(answer)
    base = len(q_toks & a_toks) / len(q_toks)
    if embedder is None:
        return round(base, 4)
    try:
        qv, av = embedder(query), embedder(answer)
        dot = sum(a * b for a, b in zip(qv, av, strict=True))
        norm = math.sqrt(sum(x * x for x in qv)) * math.sqrt(sum(x * x for x in av))
        cosine = dot / norm if norm > 0 else 0.0
        return round(0.5 * base + 0.5 * cosine, 4)
    except Exception:  # noqa: BLE001 —— embedder 异常退 n-gram
        return round(base, 4)


def context_precision(ranked: list, k: int | None = None) -> float:
    """context_precision——上下文排序质量（RAGAS CP@K 公式，无 LLM 版）

    ranked：按相关性降序排列的上下文（list[dict] 带 is_relevant bool，
    或 list[(context, is_relevant)] 元组）；元素无法解析 → 视为不相关。
    k 默认取全部。
    CP@K = Σ_{k'=1..K} (Precision@k' × rel_k') / Top-K 内相关总数。
    无相关 → 0.0。越相关排越前 → 越接近 1.0。
    """
    flags: list[bool] = []
    for item in ranked:
        if isinstance(item, dict):
            flags.append(bool(item.get("is_relevant", False)))
        elif isinstance(item, (tuple, list)) and len(item) >= 2:
            flags.append(bool(item[1]))
        else:
            flags.append(False)
    if not flags:
        return 0.0
    k = len(flags) if k is None else max(0, min(k, len(flags)))
    total_relevant = sum(flags[:k])
    if total_relevant == 0:
        return 0.0
    accum = 0.0
    seen = 0
    for pos in range(k):
        if flags[pos]:
            seen += 1
            accum += seen / (pos + 1)
    return round(accum / total_relevant, 4)


def evaluate_answer_quality(
    records: Iterable[dict],
    *,
    query_key: str = "query",
    answer_key: str = "answer",
    context_key: str = "contexts",
    relevant_key: str = "is_relevant",
) -> dict:
    """答案质量批量评估（三指标聚合，RAG 生成链路基线报告用）

    records: [{query, answer, contexts: [str 或 dict]（dict 需含 relevant_key）}]
    返回 {faithfulness, relevancy, context_precision, n, context_precision_n}。
    contexts 缺失的样本跳过 context_precision 聚合（faithfulness/relevancy 仍计入）。
    """
    n = 0
    cp_n = 0
    agg = {"faithfulness": 0.0, "relevancy": 0.0, "context_precision": 0.0}
    for rec in records:
        query = rec.get(query_key) or ""
        answer = rec.get(answer_key) or ""
        contexts = rec.get(context_key) or []
        if not answer:
            continue
        sources = [c if isinstance(c, str) else (c.get("text") or "") for c in contexts]
        agg["faithfulness"] += faithfulness(answer, sources)
        agg["relevancy"] += relevancy(query, answer)
        n += 1
        if contexts:
            cp_ranked = [c if isinstance(c, dict) else {"is_relevant": bool(c)} for c in contexts]
            agg["context_precision"] += context_precision(cp_ranked)
            cp_n += 1
    if n:
        agg["faithfulness"] = round(agg["faithfulness"] / n, 4)
        agg["relevancy"] = round(agg["relevancy"] / n, 4)
    agg["context_precision"] = round(agg["context_precision"] / cp_n, 4) if cp_n else 0.0
    agg["n"] = n
    agg["context_precision_n"] = cp_n
    return agg
