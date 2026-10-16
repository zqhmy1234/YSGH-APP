"""RAG 检索服务包（B2 设计：混合检索 + 路由分层）—— services/rag.py 拆包（F6）

管线（B2 4.1）：
  query → Query 改写（LLM：时间表达/实体抽取）→ 路由（文本/图片/混合）
  → dense+sparse 双路召回（Qdrant RRF）→ payload filter
  → bge-reranker 粗排（占位：M1 后接）→ 溯源

Mock 说明：未配置 DASHSCOPE_API_KEY 时，改写/路由走规则兜底（可测可联调），
M1 门禁（Top3≥70% + P95<3s）以规则兜底版本为基线。

模块划分（F6 拆包，2026-08-27，行为等价）：
  intent.py      意图分类与路由（_classify_query_intent / _route_query）
  rewrite.py     查询改写（_rewrite_query：时间表达/NER/LLM）
  recall.py      检索召回与组装（_merge_recalls / _boost_exact_matches /
                 _assemble_hits + 共享并发信号量 _search_semaphore）
  image.py       以图搜图（search_by_image + caption 缓存）
  pg_fallback.py Qdrant 降级 PG 全文检索（_pg_fallback_search）

对外契约不变：search / search_by_image 主入口 + 全部内部函数经本包重导出，
外部 `from app.services.rag import ...` 均保持可解析。
"""
from __future__ import annotations

import logging
import time

from app.core.config import settings
from app.schemas.search import SearchQuery, SearchResult
from app.services.embedding import encode_query
from app.services.rag.image import (
    _CAPTION_CACHE_MAX,
    _CAPTION_CACHE_TTL_SECONDS,
    _cached_image_caption,
    _caption_cache,
    _caption_cache_lock,
    _search_by_image_impl,
    search_by_image,
)
from app.services.rag.intent import _CLASS_RULES, _classify_query_intent, _route_query
from app.services.rag.pg_fallback import _pg_fallback_search
from app.services.rag.recall import (
    SEARCH_CONCURRENCY,
    _assemble_hits,
    _boost_exact_matches,
    _merge_recalls,
    _search_semaphore,
)
from app.services.rag.rewrite import _TIME_PATTERNS, _rewrite_query
from app.services.rerank import rerank, rerank_auto_enabled
from app.services.search_filters import normalize_filters
from app.services.vector_store import CONTENT_TYPE_PHOTO, get_store

logger = logging.getLogger("yishu.rag")

__all__ = [
    "SEARCH_CONCURRENCY",
    "search",
    "search_by_image",
    # intent
    "_CLASS_RULES",
    "_classify_query_intent",
    "_route_query",
    # rewrite
    "_TIME_PATTERNS",
    "_rewrite_query",
    # recall
    "_search_semaphore",
    "_merge_recalls",
    "_boost_exact_matches",
    "_assemble_hits",
    # pg_fallback
    "_pg_fallback_search",
    # image
    "_CAPTION_CACHE_MAX",
    "_CAPTION_CACHE_TTL_SECONDS",
    "_cached_image_caption",
    "_caption_cache",
    "_caption_cache_lock",
    "_search_by_image_impl",
]


def search(q: SearchQuery, db=None, user_id: str | None = None, collection: str | None = None) -> SearchResult:
    """描述性搜索主链路（API-003/RET-001~018 前置）

    user_id：溯源回填 contents 时按用户隔离（修复：原占位文本无真实内容）。
    collection：检索目标 collection（默认生产 yishu_contents；RAG 基准评测传
    yishu_benchmark 独立库，避免基准数据混入真实检索结果）。

    P2-01：并发上限（信号量阻塞排队，不放任线程池被推理占满）。
    """
    with _search_semaphore:
        return _search_impl(q, db=db, user_id=user_id, collection=collection)


def _search_impl(q: SearchQuery, db=None, user_id: str | None = None, collection: str | None = None) -> SearchResult:
    """搜索实现体（被 search 信号量包裹）"""
    start = time.perf_counter()
    degraded = False

    # 分阶段打点（2026-10-16 检索超时治理 · 立项①）：仅 want_stages=True 时计时——
    # 默认 False ⇒ 下方所有 _lap() 调用被短路，零 perf_counter 开销、零响应变化。
    # 为什么用「上一次打点到现在」的 lap 而非各阶段独立起止：管线存在回退重试
    # （NER/类目/空结果/PG 兜底）与双路召回，嵌套计时会让各阶段之和 ≠ 端到端；
    # lap 串行切片天然保证 rewrite+encode+recall(+rerank+answer) ≈ 全程墙钟。
    want_stages = q.want_stages
    _mark = [start]

    def _lap() -> int:
        now = time.perf_counter()
        delta_ms = int((now - _mark[0]) * 1000)
        _mark[0] = now
        return delta_ms

    st_rewrite: int | None = None
    st_encode: int | None = None
    st_recall: int | None = None
    st_rerank: int | None = None
    st_answer: int | None = None

    # 1. Query 改写 → 过滤条件（返回 NER 派生过滤，供空结果回退）
    rewritten, filters, ner_filters = _rewrite_query(q)
    # 用户隔离（2026-08-26 修复）：检索阶段即按 user_id 过滤（配合 vector_store._to_filter
    # 的 user_id 分支），此前仅溯源回填隔离——跨用户内容挤占召回窗口，
    # 数据多时新用户内容被挤出 top-k（api_smoke 门禁暴露）。
    if user_id:
        filters["user_id"] = str(user_id)
    # D05-3（2026-09-25 · P0）：**在降级 try 之前**校验过滤键——未知键抛
    # UnknownFilterKey（fail-closed），否则会被下面的 `except Exception` 吞掉，
    # 把"某个约束被静默丢弃"伪装成"Qdrant 降级、走 PG 兜底"。
    filters = normalize_filters(filters)

    # 2. 路由（B2：路由决定检索范围——image 意图只搜图片 caption，文字搜图）
    intent = _route_query(rewritten)
    if intent == "image":
        # FIX-1（2026-08-26）：过滤值用规范 "photo"（与生产 payload 一致；
        # 遗留 "image" 由 _to_filter 别名展开兼容）——此前 "image" 过滤
        # 在生产库恒不命中 photo 点，文字搜图/以图搜图空结果。
        filters.setdefault("content_types", [CONTENT_TYPE_PHOTO])

    # 2.5 P1-A 类目路由（2026-08-25）：text 意图 + 规则给出主导类别 → content_class
    # 过滤，把干扰类文档挡在召回路外（修复 descriptive 层召回缺口）；
    # 无主导类别/非 text 意图 → 不过滤；空结果在下方自动回退全量。
    # 修复（2026-08-25 调研）：类目判定跑【原始查询】而非改写结果——
    # 类别是用户意图属性，不应随改写漂移（改写版含"发酸"会误判 todo）。
    class_filter: str | None = None
    if settings.class_routing_enabled and intent == "text":
        class_filter = _classify_query_intent(q.q)
        if class_filter:
            filters["content_class"] = class_filter

    if want_stages:
        # rewrite 段：改写+过滤归一+路由/类目判定（均为改写层的毫秒级小步，合并计时）
        st_rewrite = _lap()

    # 3. 编码 + 召回
    try:
        dense, sparse = encode_query(rewritten)
        if want_stages:
            st_encode = _lap()  # 含 dense+sparse 双编码；mixed/双路召回的第二次编码计入 recall
        store = get_store()
        # eff_filters：最后一次成功产生 raw_hits 的过滤器（含回退后的），
        # 供双路召回的原查询路使用——此前直接复用 filters 会把回退前的
        # content_class 过滤带去原查询路，外部语料无该字段 → 原路恒空（2026-08-25 调研修复）。
        eff_filters = filters
        if intent == "mixed":
            # B2 mixed 双路融合（2026-08-19）：image 路 + 全量路并行召回 → 去重合并
            image_filters = {**filters, "content_types": [CONTENT_TYPE_PHOTO]}
            raw_hits = _merge_recalls([
                store.search(dense, sparse, filters=image_filters, limit=50, collection=collection),
                store.search(dense, sparse, filters=filters, limit=50, collection=collection),
            ], limit=50)
        else:
            raw_hits = store.search(dense, sparse, filters=filters, limit=50, collection=collection)
        # 空结果回退（2026-08-19）：仅 NER 派生过滤导致空结果时，去掉 NER 过滤重试
        # （语料缺 place/tags 元数据时防"过过滤空结果"；显式参数仍是硬约束）
        if not raw_hits and ner_filters:
            retry_filters = {k: v for k, v in filters.items() if k not in ner_filters}
            logger.info("NER 过滤空结果，回退重试（去掉 %s）", list(ner_filters))
            if intent == "mixed":
                image_filters = {**retry_filters, "content_types": [CONTENT_TYPE_PHOTO]}
                raw_hits = _merge_recalls([
                    store.search(dense, sparse, filters=image_filters, limit=50, collection=collection),
                    store.search(dense, sparse, filters=retry_filters, limit=50, collection=collection),
                ], limit=50)
            else:
                raw_hits = store.search(dense, sparse, filters=retry_filters, limit=50, collection=collection)
            eff_filters = retry_filters
        # 空结果回退（P1-A 2026-08-25）：类目过滤导致空结果 → 去掉 content_class 重试
        # （旧数据/外部语料无 content_class 字段时防误过滤；显式参数仍是硬约束）
        if not raw_hits and class_filter:
            retry_filters = {k: v for k, v in filters.items() if k != "content_class"}
            logger.info("类目过滤空结果，回退重试（去掉 content_class=%s）", class_filter)
            if intent == "mixed":
                image_filters = {**retry_filters, "content_types": [CONTENT_TYPE_PHOTO]}
                raw_hits = _merge_recalls([
                    store.search(dense, sparse, filters=image_filters, limit=50, collection=collection),
                    store.search(dense, sparse, filters=retry_filters, limit=50, collection=collection),
                ], limit=50)
            else:
                raw_hits = store.search(dense, sparse, filters=retry_filters, limit=50, collection=collection)
            eff_filters = retry_filters
        # 3.3 P0-A 双路召回（2026-08-25 调研后重写）：LLM 改写是【加性】不是替代——
        # 原查询路永远保留（用 eff_filters，与主路同口径），改写路只增不减：
        # 纠错/扩写生效时加召回，改写有害时原路兜底。此前实测 EXT recall 0.886→0.75
        # 的根因：①原路误用回退前 filters 恒空；②prompt 无差别改写短关键词。
        if rewritten != q.q and (q.q or "").strip():
            try:
                orig_dense, orig_sparse = encode_query(q.q)
                orig_hits = store.search(orig_dense, orig_sparse, filters=eff_filters, limit=50, collection=collection)
                if orig_hits:
                    raw_hits = _merge_recalls([raw_hits, orig_hits], limit=50)
            except Exception:  # noqa: BLE001 —— 原查询路失败不影响改写主路
                logger.warning("原查询双路召回失败，仅用改写路", exc_info=True)
    except Exception as exc:  # noqa: BLE001 —— Qdrant 不可用降级（API-009）
        logger.warning("Qdrant 检索降级: %s", exc)
        degraded = True
        # audit #16：降级不再返回空结果——改走 PG 全文检索兜底（ILIKE 多词 OR +
        # 命中数排序，tsvector 中文无内置 parser，ILIKE 为确定性零依赖方案）。
        raw_hits = _pg_fallback_search(q, rewritten, filters, db, user_id, q.limit)

    # 3.2 **召回为空**也走 PG 兜底（2026-10-15 · 真机 + 集成双实测「刚记的内容搜不到」）
    #
    # 根因（代码实证）：worker **先提交 `status=done`、之后才写向量**
    # （`pipeline.py:445-447`（done+commit）→ `:461-463`（索引）），而 BGE-M3 编码 + Qdrant
    # upsert 要数十秒（冷启动尤甚）⇒ 紧跟 done 的检索正落在"**DB 有、Qdrant 还没这个点**"
    # 的时间窗内；而检索只对**已召回**的命中做 DB 回查校验，**不会把 DB-only 的内容补进来**
    # （`recall.py:142-143`）⇒ 那次检索返回 `200 + hits=[]`，且 `degraded=false`
    #（该标志原只在 Qdrant 抛异常时置位）⇒ **"还没索引"被伪装成"真的没有"** ✗。
    #
    # 口径：召回为空 ⇒ 用**已存在**的 PG 兜底再捞一次（零新增依赖）。
    #  · 只有**兜底真的捞到东西**时才置 `degraded=True`（如实告知"结果来自兜底、可能不完整"）；
    #    真·无结果时**不改** degraded（否则会把"就是没有"谎报成"系统降级" ✗）；
    #  · 有召回时本段不执行 ⇒ 原有路径行为**完全不变**（零风险）。
    if not raw_hits:
        fb_hits = _pg_fallback_search(q, rewritten, filters, db, user_id, q.limit)
        if fb_hits:
            logger.info(
                "召回为空但 PG 兜底命中 %d 条（多为索引未就绪：done 已提交、向量尚未写入）",
                len(fb_hits),
            )
            degraded = True
            raw_hits = fb_hits

    # 3.5 精确命中提升（词元全命中 → 提到稠密噪声之上；描述性查询不受影响）
    raw_hits = _boost_exact_matches(rewritten, raw_hits)

    # 4. 溯源组装（RET-016：每条结果可解释命中字段）
    hits = _assemble_hits(raw_hits, q.limit, db, user_id)

    # 4.1 R-1 拒答地板（2026-10-09）：召回层稠密相似度低于地板的命中整条剔除；
    #     全部低于地板 → 空结果，即「答不出来就不答」。门禁 negative_error_rate ≤0.05。
    #     这是召回层闸门，位置刻意选在 rerank 之前：既省掉注定被丢弃候选的重排开销，
    #     也避免「LLM 精排绝不增删候选」把该拒的候选又救回来。
    #     默认 0.0 = 关闭（保持改动前行为）；标定口径见 config.retrieval_min_dense_score。
    #     注意判据用 trace["dense_score"]（稠密余弦），**不是** h.score（RRF 融合分，
    #     量级 ~0.016 且只反映名次，与相关性无关，不能用来设阈值）。
    if settings.retrieval_min_dense_score > 0 and hits:
        floor = settings.retrieval_min_dense_score
        kept = [h for h in hits if h.trace.get("dense_score", 0.0) >= floor]
        if len(kept) != len(hits):
            logger.info("R-1 拒答地板 %.3f：%d → %d 条", floor, len(hits), len(kept))
        hits = kept

    latency_ms = int((time.perf_counter() - start) * 1000)
    if want_stages:
        # recall 段：与 latency_ms 同止点（向量召回+回退重试+PG 兜底+精确提升+溯源组装+R-1 地板）
        st_recall = _lap()

    # 4.5 双层 Rerank 第一层（bge-reranker 粗排 + 低相关过滤；
    #      候选无 text/模型未就绪 → 原序）
    # 2026-08-25 RAG 审查：默认关闭（CPU ~850ms/对，50 候选 ~40s 超 P95<3s 门禁；
    # 且只重排候选集内文档，描述性查询失效根因在召回层）。
    # Wave2-F（2026-08-26）：rerank_auto_enable=True 且 GPU/模型就绪 → 自动启用
    # （rerank_auto_enabled() 内已含 settings.rerank_enabled 显式优先）。
    if rerank_auto_enabled() and hits:
        cands = [
            {"id": h.content_id, "text": h.text or "", "score": h.score, "hit": h}
            for h in hits[: settings.rerank_max_candidates]
        ]
        hits = [c["hit"] for c in rerank(rewritten, cands, min_score=settings.rerank_min_score)][: q.limit]

    # 4.5b 双层 Rerank 第二层（LLM 精排，Wave2-F 2026-08-26，B2-1 Ilya 方案）：
    #   bge 粗排 top-50→top-10（rerank_llm_candidates）→ qwen-flash 判"能否回答"
    #   → top-5（rerank_llm_top_k）。
    #   门禁：llm_rerank 内部自门控——无 key / mock / 开关关 / 解析失败 → 原序返回，
    #   精排只改变 top 顺序、绝不增删候选（防把召回有效结果挡掉）。
    #   精排判定经 base.chat_text（qwen-flash），走 llm_ops 包，不直接触碰 dashscope。
    if hits:
        from app.services.llm_ops.rerank import llm_rerank

        llm_cands = [
            {"id": h.content_id, "text": h.text or "", "score": h.score, "hit": h}
            for h in hits[: settings.rerank_llm_candidates]
        ]
        llm_reranked = llm_rerank(rewritten, llm_cands, top_k=settings.rerank_llm_top_k)
        # 仅当 LLM 真实判定过（带 rerank_reason）才替换排序；mock/无 key 原序不动候选集
        judged = {c["id"]: c.get("rerank_reason") for c in llm_reranked if "rerank_reason" in c}
        if judged and llm_reranked:
            hits = [c["hit"] for c in llm_reranked][: q.limit]
            for h in hits:
                if h.content_id in judged:
                    h.trace["llm_rerank_reason"] = judged[h.content_id]

    # 4.6 规则级敏感过滤（B5b-1 🟢：摘要/搜索规则级，不过模型；Wave1 AgentC 转交）
    #     命中 reject 类硬规则词的结果直接排除（转述用户内容的最小兜底）。
    if hits:
        try:
            from app.services.external.sensitive_words import filter_sensitive_rule

            hits = [h for h in hits if not (h.text and filter_sensitive_rule(h.text))]
        except Exception:  # noqa: BLE001 —— 敏感过滤失败不阻断搜索
            logger.warning("规则级敏感过滤异常，跳过")

    if want_stages:
        # rerank 段：latency_ms 之后的全部剩余检索工作（bge 粗排+LLM 精排+规则敏感过滤）
        st_rerank = _lap()

    # 5. 搜索式一次性问答（2026-10-16 冻结：搜索=回答卡+证据，AI 页=多轮对话）
    #    · **仅 `want_answer=True` 时触发** ⇒ 默认零 LLM 调用、零额外延迟、在野 APK 行为完全不变；
    #    · 只依据本次召回证据作答；证据不足/模型不可用 → answer=None（绝不编造，见 search_answer.py）；
    #    · LLM 未配置/超时/解析失败 → 如实置 degraded=True（复用既有降级口径，**不新增布尔位**）。
    answer: str | None = None
    answer_sources: list[str] = []
    if q.want_answer:
        from app.services.llm_ops.search_answer import generate_answer

        _ans_mark = time.perf_counter() if want_stages else 0.0
        answer, answer_sources, answer_failed = generate_answer(q.q, hits)
        if want_stages:
            st_answer = int((time.perf_counter() - _ans_mark) * 1000)
        if answer_failed:
            degraded = True
        # 回答步骤之后重算 latency_ms（协调者 2026-10-16 拍板，方案 A）：
        #   · `want_answer=False` 时本块**根本不执行** ⇒ `latency_ms` 仍取上面那次
        #     （检索段，line 239 原样），**数值与改动前逐字一致** ⇒ 在野客户端与
        #     既有 P95 验收零扰动；
        #   · `want_answer=True` 时把这次增强项的**真实尾延迟**计入 ⇒ 门禁（读 latency_ms）
        #     看到的才是端到端真相，杜绝"门禁绿、真机体感超 3s"的第二类假绿。
        latency_ms = int((time.perf_counter() - start) * 1000)

    return SearchResult(
        query=q.q,
        rewritten_query=rewritten if rewritten != q.q else None,
        intent=intent,
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
        answer=answer,
        answer_sources=answer_sources,
        # 分阶段打点：默认 want_stages=False ⇒ None（响应与改动前一致），
        # True ⇒ 五键全在，未执行/未计时的阶段如实 None（契约承诺，不编造）。
        stages=(
            {
                "rewrite": st_rewrite,
                "encode": st_encode,
                "recall": st_recall,
                "rerank": st_rerank,
                "answer": st_answer,
            }
            if want_stages
            else None
        ),
    )
