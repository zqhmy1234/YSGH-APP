"""RAG 检索召回与结果组装（RET-003 融合 / P0-A 双路 / RET-016 溯源）

拆包自 services/rag.py（F6，2026-08-27）：
  - _search_semaphore：搜索共享并发信号量（text/image 两入口共用，P2-01）
  - _merge_recalls：多路召回去重合并（mixed 双路融合）
  - _boost_exact_matches：关键词精确命中提升（字面命中压过稠密噪声）
  - _assemble_hits：溯源组装 + 用户隔离回填真实内容 + 事件级归因
"""
from __future__ import annotations

import logging
import re
import threading

from sqlalchemy import select

from app.db.models import Content, Event, EventItem
from app.schemas.search import SearchHit

logger = logging.getLogger("yishu.rag")

# P2-01 并发上限：搜索保留同步（P95<3s 门禁依赖本地推理），但用信号量限制并发
# 推理数，防止 BGE-M3(1.2GB)/reranker 同时加载打满内存、线程池被推理占满。
# 文本搜索（__init__.search）与以图搜图（image.search_by_image）共享此信号量。
SEARCH_CONCURRENCY = 4
_search_semaphore = threading.BoundedSemaphore(SEARCH_CONCURRENCY)


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
    tokens = [t for t in re.split(r"[\s,，。.！!？?、；;:：（）()「」『』【】\"'‘’]", query) if len(t) >= 2]
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
