"""rerank.py —— B2 域：第二层 LLM 精排（Ilya 方案）

任务归属：Wave 2 Agent F（M1 补遗域）独占本文件。
实现目标（B2-1）：bge-reranker 粗排 top-50→top-10 → 本模块 qwen-flash 精排 → top-5，
精排判断"这段能不能回答这个问题"，经 base.chat_text 调用，禁止直接 import dashscope。

降级契约（Ilya 方案 + 门禁对齐）：
- 无 key / mock 模式 / 开关关闭 / LLM 异常 / 输出解析失败 → 原序返回（RRF/bge 分保底），
  绝不因精排失败让搜索结果变空或抛错。
- 精排判定语义：候选与查询相关且【能回答问题】（信息量足够），不只是泛相关——
  与"召回层语义近邻"区分开，把模糊近邻压到不可回答组。
"""
from __future__ import annotations

import json
import logging
import re

from app.core.config import settings
from app.services.llm_ops.base import chat_text, llm_available

logger = logging.getLogger("yishu.llm_rerank")

# 精排系统提示：要求输出 JSON 数组，逐条判定"能否回答"，附一句理由。
# 设计约束：批量一次往返（10 候选一个 batch），qwen-flash 输出长度可控；
# 判定为二元（能回答/不能回答），理由 ≤20 字保持 token 预算。
_RERANK_SYSTEM = (
    "你是记忆检索结果的精排器。判断每条候选文本【能否回答用户的这个问题】："
    "能回答 = 内容与问题相关且包含足以回答问题的信息（时间、地点、事件、对象等）；"
    "不能回答 = 内容与问题无关，或只是语义沾边但信息不足。\n"
    '只输出 JSON 数组，不要任何其他文字，格式：[{"i": 候选序号, "ans": true或false, "reason": "一句话理由≤20字"}]'
)

# 候选文本截断上限（防超长文本打爆 prompt token；10 候选 × 300 字可控）
_MAX_CANDIDATE_CHARS = 300


def _build_prompt(query: str, candidates: list[dict]) -> str:
    """组装精排 prompt：编号候选 + 查询（截断防超长）"""
    lines = [f"问题：{query[:200]}", ""]
    for i, c in enumerate(candidates):
        text = (c.get("text") or "").strip().replace("\n", " ")[:_MAX_CANDIDATE_CHARS]
        lines.append(f"[{i}] {text}")
    lines.append("")
    lines.append('输出格式：[{"i": 0, "ans": true, "reason": "..."}]')
    return "\n".join(lines)


def _extract_json_array(answer: str) -> list[dict]:
    """容错解析 LLM 输出 → 判定数组

    qwen-flash 偶发输出 Markdown 代码围栏 / 前后缀文字；按序剥：
    1. 剥 ```json ... ``` 围栏
    2. 剥首个 [ 前的所有字符、末尾 ] 后的所有字符
    3. json.loads；失败返回 []
    """
    if not answer:
        return []
    text = answer.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        return []
    try:
        data = json.loads(text[start : end + 1])
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(data, list):
        return []
    out = []
    for item in data:
        if isinstance(item, dict) and isinstance(item.get("i"), int):
            out.append({
                "i": item["i"],
                "ans": bool(item.get("ans", False)),
                "reason": str(item.get("reason", ""))[:_MAX_CANDIDATE_CHARS],
            })
    return out


def llm_rerank(
    query: str,
    hits: list[dict],
    top_k: int | None = None,
    candidates: int | None = None,
) -> list[dict]:
    """第二层 LLM 精排：qwen-flash 批量判"这段能否回答这个问题"。

    hits: [{'id','text','score',...}, ...]（bge 粗排后 top-N）；
    返回同结构全量有序列表 = 能回答组（原分降序）在前 + 不能回答组（原分降序）在后。
    top_k（默认 settings.rerank_llm_top_k）只决定精排名次窗口：前 top_k 个写
    rerank_rank=1..top_k；全部候选附 rerank_reason（LLM 理由）。
    调用方按自身 limit 截断——精排"top-5"是名次窗口，不硬砍 API 返回条数。

    无 key / mock / 开关关 / 异常 → 原序返回（不抛异常、不改变候选集）。
    """
    if not hits:
        return hits
    if not settings.rerank_llm_enabled or not llm_available():
        return hits
    n = candidates or settings.rerank_llm_candidates
    k = top_k or settings.rerank_llm_top_k
    cands = hits[: max(1, min(n, len(hits)))]
    # 候选文本全空（溯源未回填）→ 无从精排，原序
    if not any((c.get("text") or "").strip() for c in cands):
        return list(hits)

    try:
        answer = chat_text(_RERANK_SYSTEM, _build_prompt(query, cands)).strip()
        verdicts = _extract_json_array(answer)
    except Exception as exc:  # noqa: BLE001 —— LLM 失败降级原序
        logger.warning("LLM 精排失败，原序返回: %s", exc)
        return list(hits)
    if not verdicts:
        logger.warning("LLM 精排输出解析失败，原序返回: %.80s", answer)
        return list(hits)

    # 判定 → 分组（索引越界/未判定项 → 不能回答组保底）
    score_of = {id(c): float(c.get("score") or 0.0) for c in cands}
    ok: list[tuple[dict, str]] = []
    no: list[tuple[dict, str]] = []
    for v in verdicts:
        i = v["i"]
        if not (0 <= i < len(cands)):
            continue
        (ok if v["ans"] else no).append((cands[i], v["reason"]))
    judged = {v["i"] for v in verdicts if 0 <= v["i"] < len(cands)}
    for i, c in enumerate(cands):
        if i not in judged:
            no.append((c, "未判定"))

    def _rank_key(item: tuple[dict, str]) -> float:
        return -score_of[id(item[0])]

    ok.sort(key=_rank_key)
    no.sort(key=_rank_key)

    # 组装：全量有序 + 前 top_k 记精排名次
    ordered = [c for c, _ in ok] + [c for c, _ in no]
    reasons = {id(c): r for c, r in ok}
    reasons.update({id(c): r for c, r in no})
    out: list[dict] = []
    for rank, c in enumerate(ordered, 1):
        nh = dict(c)
        nh["rerank_reason"] = reasons.get(id(c)) or "（未给出理由）"
        if rank <= k:
            nh["rerank_rank"] = rank
        out.append(nh)
    return out
