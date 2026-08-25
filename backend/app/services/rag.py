"""RAG 检索服务（B2 设计：混合检索 + 路由分层）

管线（B2 4.1）：
  query → Query 改写（LLM：时间表达/实体抽取）→ 路由（文本/图片/混合）
  → dense+sparse 双路召回（Qdrant RRF）→ payload filter
  → bge-reranker 粗排（占位：M1 后接）→ 溯源

Mock 说明：未配置 DASHSCOPE_API_KEY 时，改写/路由走规则兜底（可测可联调），
M1 门禁（Top3≥70% + P95<3s）以规则兜底版本为基线。
"""
from __future__ import annotations

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.core.config import settings
from app.db.models import Content, Event, EventItem
from app.schemas.search import SearchHit, SearchQuery, SearchResult
from app.services.embedding import encode_query
from app.services.external import rewrite_query
from app.services.ner import extract_entities
from app.services.rerank import rerank
from app.services.vector_store import CONTENT_TYPE_PHOTO, get_store

logger = logging.getLogger("yishu.rag")

# P2-01 并发上限：搜索保留同步（P95<3s 门禁依赖本地推理），但用信号量限制并发
# 推理数，防止 BGE-M3(1.2GB)/reranker 同时加载打满内存、线程池被推理占满。
SEARCH_CONCURRENCY = 4
_search_semaphore = threading.BoundedSemaphore(SEARCH_CONCURRENCY)

# 时间表达规则（"去年夏天" → 时间范围；MVP 简化）
# 2026-08-26（audit #17）扩充：去年夏天/上上周/三年前/前年/上周/前天。
# 顺序 = 优先级：长模式在前（"去年夏天" 必须先于 "去年" 匹配，break 语义下
# 首个 pos==0 命中生效）；重叠模式按最长优先排列。
_TIME_PATTERNS = [
    (re.compile(r"去年夏天"), "last_summer"),
    (re.compile(r"上上周"), "two_weeks_ago"),
    (re.compile(r"三年前"), "three_years_ago"),
    (re.compile(r"前年"), "year_before_last"),
    (re.compile(r"去年"), "last_year"),
    (re.compile(r"今年"), "this_year"),
    (re.compile(r"上个月"), "last_month"),
    (re.compile(r"上周"), "last_week"),
    (re.compile(r"前天"), "day_before_yesterday"),
    (re.compile(r"昨天"), "yesterday"),
]

# P1-A 类目路由（2026-08-25）：描述性查询 → 类别过滤，把干扰项挡在召回路外
# （审查报告短板-A："关于做产品的想法"/"让我难过的记录" Top-3 全偏，相关文档
# 排名被 voice/todo 干扰项压低，重排救不回——问题在召回层）。
# 设计约束：SetFit 单条 CPU 推理 ~27s（2026-08-20 实测）远超 P95<3s 门禁，
# 热路径用词表规则（确定性、零延迟、mock 可用）；无主导类别 → 不过滤，
# 空结果自动回退全量（与 NER 回退同模式）。LLM/SetFit 分类为后续增强。
_CLASS_RULES: dict[str, tuple[str, ...]] = {
    "emotion": ("难过", "伤心", "想哭", "开心", "高兴", "委屈", "焦虑", "孤独", "烦躁",
                "沮丧", "感动", "暖心", "心酸", "心情", "情绪", "难受", "郁闷", "后悔",
                "遗憾", "害怕", "紧张", "失望", "惊喜", "emo"),
    "idea": ("想法", "灵感", "创意", "主意", "思路", "点子", "规划", "构思", "产品", "项目"),
    "quote": ("感悟", "道理", "名言", "金句", "座右铭", "语录", "警句", "格言", "心得"),
    "todo": ("记得", "要办", "待办", "提醒", "别忘了", "买", "交", "还", "给", "预约",
              "寄", "退", "取", "回", "开会", "体检", "办", "修", "充", "清理", "更新",
              "发", "付", "缴", "房租", "购物", "采购"),
}


def _classify_query_intent(q: str) -> str | None:
    """类目路由（P1-A）：规则词表命中计数 → 主导类别；无命中/并列 → None（不过滤）"""
    if not q:
        return None
    scores = {cls: sum(1 for kw in kws if kw in q) for cls, kws in _CLASS_RULES.items()}
    best = max(scores.values())
    if best <= 0:
        return None
    winners = [cls for cls, s in scores.items() if s == best]
    return winners[0] if len(winners) == 1 else None


def _rewrite_query(q: SearchQuery) -> tuple[str, dict, dict]:
    """Query 改写（RET-007）：时间表达解析 → 过滤条件；无 LLM 时规则兜底

    返回 (rewritten, filters, ner_filters)：ner_filters 为 NER 派生过滤子集
    （place/tag），供搜索层"空结果回退"用——避免语料缺元数据时过过滤。
    """
    filters: dict = {}
    rewritten = q.q
    now = datetime.now(timezone.utc).astimezone()

    # 修复（2026-08-25 RAG 审查）：时间表达只在【句首】才算时间过滤意图。
    # 此前 pattern.search 任意位置命中即加 time 过滤并删词——"记得明天下午之前把
    # 上个月的工作总结报告交给领导" 中"上个月"是名词修饰语，不是查询意图，
    # 误加过滤 + 语料无 taken_at → 检索空结果（length 层 hit_rate 0.5 根因）。
    # 规则：时间词在句首（"去年去的地方"/"上个月的照片"）→ 时间意图，过滤+删词；
    # 句中/句尾（"我们去年去了苏州"/"把上个月的总结交了"）→ 描述性提及，不过滤。
    for pattern, kind in _TIME_PATTERNS:
        m = pattern.search(q.q)
        if m and m.start() == 0:
            if kind == "last_summer":
                # 去年夏天：去年 6/1 - 8/31（自然季窗口）
                filters["time_from"] = now.replace(year=now.year - 1, month=6, day=1)
                filters["time_to"] = now.replace(year=now.year - 1, month=8, day=31, hour=23, minute=59, second=59)
            elif kind == "two_weeks_ago":
                # 上上周：前 14 天 00:00 → 前 7 天 23:59:59（简化窗口）
                lo = (now - timedelta(days=14)).replace(hour=0, minute=0, second=0)
                hi = (now - timedelta(days=7)).replace(hour=23, minute=59, second=59)
                filters["time_from"] = lo
                filters["time_to"] = hi
            elif kind == "three_years_ago":
                filters["time_from"] = now.replace(year=now.year - 3, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 3, month=12, day=31, hour=23, minute=59, second=59)
            elif kind == "year_before_last":
                filters["time_from"] = now.replace(year=now.year - 2, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 2, month=12, day=31, hour=23, minute=59, second=59)
            elif kind == "last_week":
                lo = (now - timedelta(days=7)).replace(hour=0, minute=0, second=0)
                hi = (now - timedelta(days=1)).replace(hour=23, minute=59, second=59)
                filters["time_from"] = lo
                filters["time_to"] = hi
            elif kind == "day_before_yesterday":
                d = now - timedelta(days=2)
                filters["time_from"] = d.replace(hour=0, minute=0, second=0)
                filters["time_to"] = d.replace(hour=23, minute=59, second=59)
            elif kind == "last_year":
                filters["time_from"] = now.replace(year=now.year - 1, month=1, day=1)
                filters["time_to"] = now.replace(year=now.year - 1, month=12, day=31)
            elif kind == "this_year":
                filters["time_from"] = now.replace(month=1, day=1)
            elif kind == "last_month":
                first = now.replace(day=1)
                if first.month > 1:
                    prev = first.replace(month=first.month - 1)
                else:
                    prev = first.replace(year=first.year - 1, month=12)
                filters["time_from"] = prev
                filters["time_to"] = first
            elif kind == "yesterday":
                y = now - timedelta(days=1)
                filters["time_from"] = y.replace(hour=0, minute=0, second=0)
                filters["time_to"] = y.replace(hour=23, minute=59, second=59)
            rewritten = pattern.sub("", q.q).strip()
            break

    # LLM 改写（S1-03 百炼接入：配置 key 后启用；未配置/失败 → 规则结果兜底）
    # 修复（审查 MINOR）：LLM 输入用规则改写后的文本（rewritten），避免
    # LLM 把已删的时间词带回原文（filter 已生效，文本却含"去年"造成语义噪音）
    # 2026-08-25 实测：llm_rewrite_enabled 默认关（短关键词改写伤害，见 config 注释）；
    # 开启时仍走双路召回（rag.py 3.3），改写路只增不减。
    if settings.llm_rewrite_enabled:
        try:
            llm_q = rewrite_query(rewritten)
            if llm_q:
                rewritten = llm_q
        except RuntimeError:
            pass

    if q.content_types:
        filters["content_types"] = q.content_types
    if q.time_from:
        filters["time_from"] = q.time_from
    if q.time_to:
        filters["time_to"] = q.time_to
    if q.place:
        filters["place"] = q.place
    if q.tag:
        filters["tag"] = q.tag

    # B2-2 NER 实体抽取（2026-08-19）：查询"苏州"/"小张" → place/tag 过滤
    # 规则词表起步（零延迟零成本，P95<3s 门禁不增 LLM 往返）；LLM 兜底默认关，
    # 待延迟预算评估后开（enable_llm=True）。显式参数优先于 NER 抽取结果。
    entities = extract_entities(rewritten)
    ner_filters: dict = {}
    if entities.get("place") and not filters.get("place"):
        filters["place"] = entities["place"]
        ner_filters["place"] = entities["place"]
    if entities.get("person") and not filters.get("tag"):
        filters["tag"] = entities["person"]
        ner_filters["tag"] = entities["person"]

    return rewritten, filters, ner_filters


def _route_query(q: str) -> str:
    """查询路由（B2：文本/图片/混合意图；规则词表，确定性零延迟）

    2026-08-25 调研：LLM 路由实测有害——"货车保险杠前面加装的灯叫什么"被
    误判为 image 意图 → 过滤全部 text 文档 → 空结果（探针复现）。规则词表
    覆盖常见图片表达且 route_acc=1.0（PASS），路由保持规则版。
    """
    # 规则兜底：图片意图关键词（B2 路由；词表增强 WP-F：扩充到常见图片表达）
    image_hints = [
        "照片", "图片", "拍的", "截图", "这张", "图里", "壁纸", "表情包",
        "相册", "抓拍", "合照", "自拍", "风景照", "图片里", "照片里",
    ]
    if any(h in q for h in image_hints):
        return "image"
    return "text"


def _merge_recalls(recalls: list[list[dict]], limit: int = 50) -> list[dict]:
    """多路召回合并：按 content_id 去重保留最高分（mixed 双路融合用）"""
    merged: dict[str, dict] = {}
    for hits in recalls:
        for hit in hits:
            cid = hit["content_id"]
            if cid not in merged or hit["score"] > merged[cid]["score"]:
                merged[cid] = hit
    return sorted(merged.values(), key=lambda x: x["score"], reverse=True)[:limit]


def _boost_exact_matches(query: str, raw_hits: list[dict]) -> list[dict]:
    """关键词精确命中提升（2026-08-25 RAG 审查新增）

    问题（探针实锤）："马拉松""收房租"等字面关键词查询，RRF 里 dense 路权重 0.7，
    语义近邻（todo/quote 等无关键词文档）排名压过 sparse 精确命中文档 →
    字面命中被稠密噪声稀释（probe：马拉松 top3 里精确命中的 emotion 文档排第 3）。

    规则：rewritten 的全部词元（长度≥2，按标点/空白切分）都出现在文档原文 →
    精确命中，score ×1.8 后重排；部分词元命中（≥50%）→ ×1.3（P0-D 梯度，
    2026-08-25：2/3 词命中给中等提升，长关键词查询不再只有全命中/无命中两档）。
    描述性查询（"关于做产品的想法"）无文档能全词命中 → 不触发，零副作用；
    单 token 查询（"马拉松"/"买牛奶"）同样受益（全命中走 ×1.8）。
    """
    if not raw_hits or not query:
        return raw_hits
    tokens = [t for t in re.split(r"[\s,，。.！!？?、；;：:（）()「」『』【】\"'‘’]", query) if len(t) >= 2]
    if not tokens:
        return raw_hits
    # 拷贝后修改（2026-08-25 测试暴露：原地改 score 会污染调用方复用的列表）
    out: list[dict] = []
    for h in raw_hits:
        nh = dict(h)
        text = nh.get("text") or ""
        if text:
            matched = [t for t in tokens if t in text]
            if len(matched) == len(tokens):
                nh["score"] = round(float(nh["score"]) * 1.8, 4)
            elif len(matched) / len(tokens) >= 0.5:
                nh["score"] = round(float(nh["score"]) * 1.3, 4)
        out.append(nh)
    return sorted(out, key=lambda x: float(x["score"]), reverse=True)


def _assemble_hits(raw_hits: list[dict], limit: int, db, user_id: str | None) -> list[SearchHit]:
    """溯源组装（RET-016：每条结果可解释命中字段；按用户隔离回填真实内容）

    audit #15（2026-08-26）：事件级归因——回填 event_id/event_title（B3 事件
    聚合已落库 events/event_items；未关联事件的内容两字段保持 None）。
    """
    hits: list[SearchHit] = []
    if not raw_hits:
        return hits
    content_ids = [rh["content_id"] for rh in raw_hits[:limit]]
    content_map: dict[str, Content] = {}
    event_map: dict[str, dict] = {}
    if db is not None and user_id is not None:
        # 过滤非 UUID 格式 id（UUID 列无法匹配 rag-001 类测试点，防 PG 报错）
        _uuid_re = re.compile(
            r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
        )
        valid_ids = [cid for cid in content_ids if _uuid_re.match(cid)]
        if valid_ids:
            rows = db.execute(
                select(Content).where(
                    Content.id.in_(valid_ids),
                    Content.user_id == user_id,
                )
            ).scalars().all()
            content_map = {str(c.id): c for c in rows}
            # 事件级归因：content → event_items → events（用户隔离 + 软删过滤）
            try:
                ev_rows = db.execute(
                    select(EventItem.content_id, Event.id, Event.title)
                    .join(Event, Event.id == EventItem.event_id)
                    .where(
                        EventItem.content_id.in_(valid_ids),
                        Event.user_id == user_id,
                        Event.deleted_at.is_(None),
                    )
                ).all()
                event_map = {
                    str(r.content_id): {"id": str(r.id), "title": r.title}
                    for r in ev_rows
                }
            except Exception:  # noqa: BLE001 —— 事件归因失败不影响溯源主链路
                logger.warning("事件归因回填失败", exc_info=True)
    for rh in raw_hits[:limit]:
        c = content_map.get(rh["content_id"])
        matched = []
        if rh.get("pg"):
            matched.append("pg")
        else:
            if rh["dense_score"] > 0:
                matched.append("dense")
            if rh["sparse_score"] > 0:
                matched.append("sparse")
        ev = event_map.get(rh["content_id"])
        hits.append(SearchHit(
            content_id=rh["content_id"],
            content_type=c.content_type if c else "text",
            text=(c.text if c else None) or rh.get("text"),
            taken_at=c.taken_at if c else None,
            place=c.place if c else None,
            event_id=ev["id"] if ev else None,
            event_title=ev["title"] if ev else None,
            score=rh["score"],
            trace={
                "matched": matched or ["dense"],
                "dense_score": rh["dense_score"],
                "sparse_score": rh["sparse_score"],
                "rrf": rh["score"],
            },
        ))
    return hits


def _pg_fallback_search(
    q: SearchQuery,
    rewritten: str,
    filters: dict,
    db,
    user_id: str | None,
    limit: int,
) -> list[dict]:
    """Qdrant 降级 → PG 全文检索兜底（audit #16，API-009 降级不再空结果）

    中文无内置 tsvector parser（PG 默认分词按空格），用 ILIKE 多词 OR + 命中
    词元数排序（确定性、零依赖、mock 可用）。支持 content_type/时间/place/
    tag(ci_tags jsonb) 过滤与用户隔离；db/user_id 缺失（纯逻辑测试）返回 []。
    返回结构与向量检索 hit 同构（多带 "pg" 标记，trace 显示真实通道）。
    """
    if db is None or user_id is None:
        return []
    tokens = [
        t for t in re.split(r"[\s,，。.！!？?、；;:：（）()「」『』【】\"'‘’]", rewritten or "")
        if len(t) >= 2
    ]
    if not tokens:
        return []
    from sqlalchemy import or_

    stmt = select(Content).where(
        Content.user_id == user_id,
        Content.deleted_at.is_(None),
    )
    cts = filters.get("content_types")
    if cts:
        # FIX-1 同口径："image" 别名 → 规范 "photo"（生产 photo 点即 "photo"）
        from app.services.vector_store import CONTENT_TYPE_ALIASES

        stmt = stmt.where(Content.content_type.in_([CONTENT_TYPE_ALIASES.get(c, c) for c in cts]))
    if filters.get("content_class"):
        stmt = stmt.where(Content.content_class == filters["content_class"])
    if filters.get("time_from"):
        stmt = stmt.where(Content.taken_at >= filters["time_from"])
    if filters.get("time_to"):
        stmt = stmt.where(Content.taken_at <= filters["time_to"])
    if filters.get("place"):
        stmt = stmt.where(Content.place == filters["place"])
    if filters.get("tag"):
        # ci_tags 为 JSONB list[str]：cast to text 后 ILIKE（近似包含匹配）
        stmt = stmt.where(Content.extra["ci_tags"].astext.ilike(f"%{filters['tag']}%"))
    stmt = stmt.where(or_(*[Content.text.ilike(f"%{t}%") for t in tokens]))
    try:
        rows = db.execute(stmt).scalars().all()
    except Exception:  # noqa: BLE001 —— PG 兜底自身失败 → 空结果（不再抛）
        logger.warning("PG 兜底检索失败", exc_info=True)
        return []

    def _rank(c: Content) -> tuple[int, datetime]:
        text = c.text or ""
        return (sum(1 for t in tokens if t in text), c.taken_at or datetime.min.replace(tzinfo=timezone.utc))

    rows.sort(key=_rank, reverse=True)
    out = []
    for i, c in enumerate(rows[:limit]):
        out.append({
            "content_id": str(c.id),
            "score": round(max(0.0, 1.0 - i * 0.01), 4),
            "dense_score": 0.0,
            "sparse_score": 0.0,
            "text": c.text,
            "pg": True,
        })
    return out


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

    # 1. Query 改写 → 过滤条件（返回 NER 派生过滤，供空结果回退）
    rewritten, filters, ner_filters = _rewrite_query(q)
    # 用户隔离（2026-08-26 修复）：检索阶段即按 user_id 过滤（配合 vector_store._to_filter
    # 的 user_id 分支），此前仅溯源回填隔离——跨用户内容挤占召回窗口，
    # 数据多时新用户内容被挤出 top-k（api_smoke 门禁暴露）。
    if user_id:
        filters["user_id"] = str(user_id)

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

    # 3. 编码 + 召回
    try:
        dense, sparse = encode_query(rewritten)
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

    # 3.5 精确命中提升（词元全命中 → 提到稠密噪声之上；描述性查询不受影响）
    raw_hits = _boost_exact_matches(rewritten, raw_hits)

    # 4. 溯源组装（RET-016：每条结果可解释命中字段）
    hits = _assemble_hits(raw_hits, q.limit, db, user_id)

    latency_ms = int((time.perf_counter() - start) * 1000)

    # 4.5 双层 Rerank 第一层（bge-reranker 粗排 + 低相关过滤；
    #      候选无 text/模型未就绪 → 原序）
    # 2026-08-25 RAG 审查：默认关闭（CPU ~850ms/对，50 候选 ~40s 超 P95<3s 门禁；
    # 且只重排候选集内文档，描述性查询失效根因在召回层）。GPU 部署时置
    # settings.rerank_enabled=true 启用，候选数限制在 rerank_max_candidates 内。
    if settings.rerank_enabled and hits:
        cands = [
            {"id": h.content_id, "text": h.text or "", "score": h.score, "hit": h}
            for h in hits[: settings.rerank_max_candidates]
        ]
        hits = [c["hit"] for c in rerank(rewritten, cands, min_score=settings.rerank_min_score)][: q.limit]

    return SearchResult(
        query=q.q,
        rewritten_query=rewritten if rewritten != q.q else None,
        intent=intent,
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
    )


def search_by_image(
    image_path: str,
    q: SearchQuery,
    db=None,
    user_id: str | None = None,
    collection: str | None = None,
) -> SearchResult:
    """以图搜图（B2-4 · 2026-08-19）：图片 → Qwen3-VL caption → BGE-M3 向量 → image_vec 检索

    P2-01：与 search 共享并发信号量（Qwen3-VL + 编码均为重推理）。
    """
    with _search_semaphore:
        return _search_by_image_impl(image_path, q, db=db, user_id=user_id, collection=collection)


def _search_by_image_impl(
    image_path: str,
    q: SearchQuery,
    db=None,
    user_id: str | None = None,
    collection: str | None = None,
) -> SearchResult:
    """以图搜图实现体（被 search_by_image 信号量包裹）

    caption 向量化方案（B2-4 允许的替代路径；tongyi-embedding-vision-plus 开通后可替换）。
    返回结构同描述性搜索（intent=image）。

    P95 优化（audit #8 · 2026-08-26）：按图片字节 sha256 缓存 caption（进程内
    TTL 24h），重复同图查询跳过 qwen3-vl-plus 往返（单次 2-4.4s）→ 缓存命中时
    只剩编码+检索（~1s）。换 Qwen3-VL-Embedding（tongyi-embedding-vision-plus）
    属 B2-4 需求，需 key，登记不阻塞。
    """
    from app.services.embedding import encode_dense

    start = time.perf_counter()
    degraded = False
    caption = ""
    try:
        caption = _cached_image_caption(image_path)
        if not caption:
            raise RuntimeError("图片 caption 为空")
        vec = encode_dense([caption])[0]
        store = get_store()
        # FIX-1：过滤值用规范 "photo"（遗留 "image" 由 _to_filter 别名兼容）
        filters: dict = {"content_types": [CONTENT_TYPE_PHOTO]}
        if user_id:
            filters["user_id"] = str(user_id)
        raw_hits = store.search_image(vec, filters=filters, limit=50, collection=collection)
    except Exception as exc:  # noqa: BLE001 —— 图片塔/向量库不可用降级
        logger.warning("以图搜图降级: %s", exc)
        degraded = True
        caption = ""
        raw_hits = []

    hits = _assemble_hits(raw_hits, q.limit, db, user_id)
    latency_ms = int((time.perf_counter() - start) * 1000)
    return SearchResult(
        query=q.q,
        rewritten_query=caption or None,
        intent="image",
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
    )


# ---- 以图搜图 caption 缓存（P95 优化；进程内 LRU + TTL，零依赖） ----
_CAPTION_CACHE_MAX = 256
_CAPTION_CACHE_TTL_SECONDS = 24 * 3600
_caption_cache: dict[str, tuple[float, str]] = {}
_caption_cache_lock = threading.Lock()


def _cached_image_caption(image_path: str) -> str:
    """图片 → caption（按字节 sha256 缓存；重复查询跳过 VL 往返）

    缓存未命中 → 调 image_caption（qwen3-vl-plus）；失败抛异常由调用方降级。
    缓存在进程内共享（get_store 同生命周期），超 TTL/超上限自动淘汰。
    """
    import hashlib

    try:
        with open(image_path, "rb") as f:
            digest = hashlib.sha256(f.read()).hexdigest()
    except OSError:
        # 路径不可读（含测试注入的假路径）→ 跳过缓存，直接透传（保持原契约）
        from app.services.external.dashscope import image_caption as _vl_caption

        return _vl_caption(image_path).strip()
    now = time.time()
    with _caption_cache_lock:
        hit = _caption_cache.get(digest)
        if hit and now - hit[0] < _CAPTION_CACHE_TTL_SECONDS:
            return hit[1]
    from app.services.external.dashscope import image_caption as _vl_caption

    caption = _vl_caption(image_path).strip()
    with _caption_cache_lock:
        if len(_caption_cache) >= _CAPTION_CACHE_MAX:
            # 简单淘汰：清掉最早一半（零依赖，够用）
            for k in sorted(_caption_cache, key=lambda x: _caption_cache[x][0])[: _CAPTION_CACHE_MAX // 2]:
                _caption_cache.pop(k, None)
        _caption_cache[digest] = (now, caption)
    return caption
