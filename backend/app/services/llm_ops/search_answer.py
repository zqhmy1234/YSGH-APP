"""search_answer.py —— B2/F5 搜索式一次性问答（回答卡 + 证据）。

## 产品口径（2026-10-16 冻结）

用户在真机反馈「AI 页冒出 JSON」后，方向定为：**搜索 = 一次性问答（回答卡 + 证据），
AI 页 = 多轮对话**。故 `POST /api/v1/search` 在 `want_answer=True` 时，在**本次召回
证据**上生成一句短答，并返回其引用的证据 `content_id`（供客户端把每条证据挂成卡片）。

## 铁律对齐（本仓既有纪律）

1. **只依据本次召回的证据作答**：prompt 只喂 `hits` 的文本/时间/地点。证据不足
   （无可用文本）或模型判不足以回答 → `answer=None`，**绝不编造**。
2. **失败不静默、降级可观测**：LLM 未配置/mock/超时/解析失败 → `answer=None` 且
   返回 `failed=True`（调用方并入既有 `SearchResult.degraded`，**不新增布尔位**）。
   反之「证据不足」属**正常无答案**，**不得**置 degraded（否则把"就是没有"谎报成"系统降级"）。
3. **回答必过 `strip_model_markup`**：2026-10-15 真机「AI 对话冒 JSON」事故的单点防线，
   回答落用户前（含落库前）一律剥离 DSML/工具调用标记。
4. **`answer_sources` ⊆ 命中集**：模型只能引用给定条目序号，越界/非法序号一律丢弃。
5. **超时预算**：整条 `/search` 的 P95 验收线是 **<3s**（`api/search.py` 注释），回答是
   **增强项** ⇒ 只配 **~1/3** 的预算，取 **1.0s** 小超时（`ANSWER_TIMEOUT_SECONDS`），
   拿不到就 `answer=None`，**不拖垮主链路、不等后台线程**。产品口径：检索链路本身已占
   相当预算，**宁可多一次"无回答"，也不让主链路越线**（异常/慢尾一律降级）。
6. **`want_answer=False` 不得多一次 LLM 调用**：门控在 `rag._search_impl`，默认零成本。

## 调用约定

- 统一经 `llm_ops.base.chat_text`（已收敛鉴权/重试/DeepSeek 切换），**不直接 import dashscope**。
- 测试打桩目标为本模块的 `chat_text` / `llm_available`（与 `llm_ops.rerank` 同款约定）。
"""
from __future__ import annotations

import concurrent.futures as _cf
import logging

from app.schemas.search import SearchHit
from app.services.llm_ops.base import chat_text, llm_available
from app.services.llm_ops.parsing import extract_json_object
from app.services.model_text import strip_model_markup

logger = logging.getLogger("yishu.search_answer")

# 单次回答的 LLM 超时预算（秒）。
#
# 2026-10-24（待办总账 A-11 · 用户拍板「按推荐 1+3」）：**1.0 → 2.5**。
#
# 为什么改（实测，不是估计）：
#   本机真 LLM（DeepSeek）跑 3 条候选语料，上限 1.0s 时 **3/3 全部超时降级**
#   （`failed=True`、`answer=''`，耗时被锯在 1.00~1.02s）；
#   把上限放宽到 8s 后 **2/2 成功**，真实耗时 **1.35s / 1.75s**，回答质量正常
#   （「你只在西湖边荷花开了时拍过好多照片，没有提到具体日期」）。
#   ⇒ 结论：**瓶颈是 1.0s 这把锯子，不是回答链本身太慢**。1.0s 等于让回答卡永远出不来。
#
# 为什么是 2.5s 而不是更大：2.5s 覆盖实测 1.35~1.75s 并留约 40% 余量，
#   同时**受 P95<3s 预算约束**（检索段本身已超预算，见 D-04 治理）。上限再抬就是
#   用整条搜索的尾延迟换回答卡，属产品取舍，本次不擅自越界。
#
# ⚠️ 这**不是**"放宽验收口径换绿"——它是把增强项的超时从"必然失败"调到"实测可交付"。
#   验收线（P95<3s）与门禁阈值一字未动；相反，因为回答步骤之后会重算 latency_ms
#   （`rag/__init__.py`），这 1.35~1.75s 会**如实计入**门禁读数，不会虚假变绿。
ANSWER_TIMEOUT_SECONDS = 2.5

# 回答长度上限（字）——产品要求"一两句中文"；超出按上限截断（保护回答卡版式）。
_MAX_ANSWER_CHARS = 120

# 单条证据文本截断上限（防超长文本打爆 prompt token）。
_MAX_EVIDENCE_CHARS = 200

# 送入 prompt 的证据条数上限（时间/token 预算；hits 本身可达 limit=50）。
_MAX_EVIDENCE_ITEMS = 10

# 回答系统提示：只依据证据、可判"答不了"、只输出 JSON、短。
_ANSWER_SYSTEM = (
    "你是个人记忆助手。用户会把他的问题和若干条他自己的记忆条目一起给你。"
    "请【只依据给定的记忆条目】用简体中文回答，一两句话、不超过120字；"
    "若给定条目不足以回答，answer 返回空字符串，绝不可编造或补充未出现的信息。"
    "只输出 JSON 对象，不要任何其他文字，格式："
    '{"answer": "回答文本", "sources": [引用到的条目序号（整数）,...]}'
)


def _coerce_index(value: object) -> int | None:
    """把模型给出的条目序号归一为 int（接受整数与数字字符串；bool 显式排除）。"""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        s = value.strip()
        if s.lstrip("-").isdigit():
            return int(s)
    return None


def _build_prompt(query: str, evidence: list[SearchHit]) -> str:
    """组装回答 prompt：编号证据（文本 + 时间 + 地点；截断防超长）。"""
    lines = [f"用户的问题：{(query or '')[:200]}", "", "用户自己的记忆条目："]
    for i, h in enumerate(evidence):
        text = (h.text or "").strip().replace("\n", " ")[:_MAX_EVIDENCE_CHARS]
        meta: list[str] = []
        if h.taken_at is not None:
            meta.append(f"时间：{h.taken_at:%Y-%m-%d}")
        if h.place:
            meta.append(f"地点：{h.place}")
        meta_str = f"（{'，'.join(meta)}）" if meta else ""
        lines.append(f"[{i}] {text}{meta_str}")
    lines.append("")
    lines.append('输出格式：{"answer": "...", "sources": [序号,...]}')
    return "\n".join(lines)


def _call_with_timeout(user_prompt: str) -> str:
    """带小超时的 `chat_text` 调用（阻塞 SDK 无法中断 ⇒ 线程池 + future.result 超时）。

    与 `external/retry.py` 同款策略：`shutdown(wait=False)` 不等超时线程，
    超时即抛 `TimeoutError` 交调用方降级为 `answer=None`。
    """
    pool = _cf.ThreadPoolExecutor(max_workers=1)
    try:
        future = pool.submit(chat_text, _ANSWER_SYSTEM, user_prompt)
        try:
            return future.result(timeout=ANSWER_TIMEOUT_SECONDS)
        except _cf.TimeoutError:
            future.cancel()
            raise TimeoutError(
                f"search_answer 回答超时 {ANSWER_TIMEOUT_SECONDS}s"
            ) from None
    finally:
        pool.shutdown(wait=False, cancel_futures=True)


def _sanitize_answer(text: str) -> str:
    """清洗回答：剥结构化标记 → 单行 → 长度上限。"""
    cleaned = strip_model_markup(text or "")
    cleaned = cleaned.replace("\n", " ").strip()
    if len(cleaned) > _MAX_ANSWER_CHARS:
        cleaned = cleaned[:_MAX_ANSWER_CHARS].rstrip()
    return cleaned


def _map_sources(raw_sources: object, evidence: list[SearchHit]) -> list[str]:
    """条目序号 → `content_id`；越界/非法/重复序号一律丢弃，保证结果为命中集子集。"""
    if not isinstance(raw_sources, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in raw_sources:
        idx = _coerce_index(item)
        if idx is None or not (0 <= idx < len(evidence)):
            continue
        cid = evidence[idx].content_id
        if cid in seen:
            continue
        seen.add(cid)
        out.append(cid)
    return out


def generate_answer(
    query: str, hits: list[SearchHit]
) -> tuple[str | None, list[str], bool]:
    """在本次召回证据上生成一次性回答。

    返回 `(answer, answer_sources, failed)`：

      - `answer`：用户可见短答（已过 `strip_model_markup`）；无答案 → None。
      - `answer_sources`：引用证据的 `content_id`（**⊆ hits.content_id**）。
      - `failed`：**系统降级**标记 —— LLM 不可用/超时/解析失败为 True（调用方置
        `degraded=True`）；「证据不足」等**正常无答案**为 False（**不得**置 degraded）。
    """
    # 证据不足（无任何可用文本）⇒ 不作答。**先于 LLM 可用性判断**：此时无答案
    # 是"本来就没有"，不是"系统降级"，不得置 degraded。
    evidence = [h for h in hits if (h.text or "").strip()][:_MAX_EVIDENCE_ITEMS]
    if not evidence:
        return None, [], False

    if not llm_available():
        # 模型不可用（未配 key / mock）⇒ 降级（可观测）。
        return None, [], True

    try:
        raw = _call_with_timeout(_build_prompt(query, evidence))
    except Exception as exc:  # noqa: BLE001 —— 超时/网络/其它异常一律降级，不抛给调用方
        logger.warning("搜索回答 LLM 调用失败/超时，answer=None: %s", exc)
        return None, [], True

    # 要求 JSON 对象且带 answer 字段；解析失败 ⇒ 降级（口径见模块文档第 2 条）。
    obj = extract_json_object(raw)
    answer_raw = obj.get("answer")
    if not isinstance(answer_raw, str):
        logger.warning("搜索回答解析失败（无 answer 字段），answer=None: %.80s", raw)
        return None, [], True

    answer = _sanitize_answer(answer_raw)
    if not answer:
        # 模型判"证据不足以回答" ⇒ 正常无答案（非降级）。
        return None, [], False

    return answer, _map_sources(obj.get("sources"), evidence), False
