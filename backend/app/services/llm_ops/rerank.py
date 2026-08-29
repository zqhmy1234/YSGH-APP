"""rerank.py —— B2 域：第二层 LLM 精排（Ilya 方案）

任务归属：Wave 2 Agent F（M1 补遗域）独占本文件。
实现目标（B2-1）：bge-reranker 粗排 top-50→top-10 → 本模块 qwen-flash 精排 → top-5，
精排判断"这段能不能回答这个问题"，经 base.chat_text 调用，禁止直接 import dashscope。

降级契约（Ilya 方案 + 门禁对齐）：
- 无 key / mock 模式 / 开关关闭 / LLM 异常 / 输出解析失败 → 原序返回（RRF/bge 分保底），
  绝不因精排失败让搜索结果变空或抛错。
- 精排判定语义：候选与查询相关且【能回答问题】（信息量足够），不只是泛相关——
  与"召回层语义近邻"区分开，把模糊近邻压到不可回答组。

鲁棒性加固（2026-08-29 百炼真实链路加固·F5）：08-28 真实评测实证
「llm_rerank 逐查询调用、输出解析失败回退原序」——qwen-flash 输出并非恒
标准 JSON：ans 字符串化（bool("false")==True 会错误换序）、数组被 max_tokens
截断、全角标点混入、i 输出成字符串、单候选输出对象而非数组。解析层改为
「标准解析 → 逐块打捞提取 → 字段归一」三级兜底，消除静默空转与真伪值误判。
"""
from __future__ import annotations

import json
import logging
import re

from app.core.config import settings
from app.services.llm_ops.base import chat_text, llm_available
from app.services.llm_ops.parsing import extract_json_array

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


# ---- 输出解析加固（2026-08-29 百炼真实链路加固） ----

# ans 真伪值归一集合：qwen-flash 偶发把 ans 写成字符串/中文（"false"/"是"）。
# 直接 bool("false") == True 是最阴险的误判（非空字符串恒真 → 无关候选被顶到前排）。
_ANS_TRUE_TEXTS = {"true", "1", "yes", "y", "是", "对", "能", "可以", "是的", "能够回答"}
_ANS_FALSE_TEXTS = {"false", "0", "no", "n", "否", "不", "不能", "不可以", "不是", "无关", "无法回答"}

# 打捞提取：独立 {...} 块（截断数组的尾块不完整，交给 _TAIL_RE 补收）
_BLOCK_RE = re.compile(r"\{[^{}]*\}")
_TAIL_RE = re.compile(r"\{[^{}]*$")
_I_RE = re.compile(r'"i"\s*[:：]\s*"?(-?\d+)"?')
_ANS_RE = re.compile(r'"ans"\s*[:：]\s*"?([A-Za-z]+|[01]|[\u4e00-\u9fff]+)"?', re.IGNORECASE)
_REASON_RE = re.compile(r'"reason"\s*[:：]\s*"([^"]*)"')


def _norm_ans_opt(value: object) -> bool | None:
    """ans 真伪判定（可识别才给结论）：bool/数字直判；字符串归一后查同义集合；
    无法识别（含截断前缀 "tru"）→ None（salvage 路径据此放弃该块，宁不判不冒换序风险）"""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        s = value.strip().lower().strip("\"'")
        if s in _ANS_TRUE_TEXTS:
            return True
        if s in _ANS_FALSE_TEXTS:
            return False
    return None


def _norm_ans(value: object) -> bool:
    """ans 归一（消费路径）：未知值 → False（不能回答组保底，与原 bool() 契约保守一致）"""
    return _norm_ans_opt(value) is True


def _norm_i(value: object) -> int | None:
    """i 字段归一（int 与 "3" 字符串均接受；bool 显式排除，防 True→1 假索引）"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip().strip("\"'")
        if s.lstrip("-").isdigit():
            return int(s)
    return None


def _salvage_blocks(answer: str) -> list[dict]:
    """整数组解析失败（截断/全角标点/散文混入）时的逐块打捞

    逐 {...} 块先试 json.loads，失败退正则提取 i/ans/reason；数组截断时尾部
    不完整块（无 `}`）也尽力抢救 i/ans（reason 常为截断牺牲品，可缺）。
    丢 i 的块与无法正则识别的块丢弃（调用方按未判定 → 不能回答组保底）。
    """
    items: list[dict] = []
    blocks = _BLOCK_RE.findall(answer)
    tail = _TAIL_RE.search(answer)
    if tail:
        blocks.append(tail.group(0))
    for blk in blocks:
        try:
            obj = json.loads(blk)
        except json.JSONDecodeError:
            obj = None
        if isinstance(obj, dict):
            items.append(obj)
            continue
        m_i = _I_RE.search(blk)
        if not m_i:
            continue
        m_a = _ANS_RE.search(blk)
        if not m_a or _norm_ans_opt(m_a.group(1)) is None:
            continue  # ans 缺失或截断成未知前缀（如 "tru"）→ 宁可不判，不冒换序风险
        item: dict = {"i": int(m_i.group(1)), "ans": m_a.group(1)}
        m_r = _REASON_RE.search(blk)
        if m_r:
            item["reason"] = m_r.group(1)
        items.append(item)
    return items


def _extract_json_array(answer: str) -> list[dict]:
    """容错解析 LLM 输出 → 判定数组（字段级清洗保留在 rerank）

    三级兜底（2026-08-29 加固）：
    1. 标准路径：围栏剥离/数组切片统一走 llm_ops.parsing.extract_json_array（S1-H1 收口）；
    2. 打捞路径：整数组解析失败 → _salvage_blocks 逐块正则提取（截断/全角/散文）；
    3. 字段归一：i 接受字符串数字；ans 走 _norm_ans（字符串 "false" 不再被 bool() 误判真）。
    """
    data = extract_json_array(answer)
    if not data:
        data = _salvage_blocks(answer)
    out: list[dict] = []
    seen: set[int] = set()
    for item in data:
        if not isinstance(item, dict):
            continue
        i = _norm_i(item.get("i"))
        if i is None or i in seen:
            continue
        seen.add(i)
        out.append({
            "i": i,
            "ans": _norm_ans(item.get("ans", False)),
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
