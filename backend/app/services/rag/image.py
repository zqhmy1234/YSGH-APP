"""以图搜图（B2-4 · 2026-08-19）：图片 → Qwen3-VL caption → BGE-M3 向量 → image_vec 检索

拆包自 services/rag.py（F6，2026-08-27）：
  - search_by_image：以图搜图主入口（与文本 search 共享并发信号量）
  - caption 缓存（P95 优化，audit #8：按图片字节 sha256 进程内 TTL 24h）
  - 2026-08-29 真实链路加固：VL 最终失败降级用过期缓存兜底（08-28 评测
    2/10 miss 系 qwen3-vl 连接重置 → 空结果，网络抖动期检索可用性优先）
"""
from __future__ import annotations

import logging
import threading
import time

from app.schemas.search import SearchQuery, SearchResult
from app.services.rag.recall import _assemble_hits, _search_semaphore
from app.services.vector_store import CONTENT_TYPE_PHOTO, get_store

logger = logging.getLogger("yishu.rag")

# ---- 以图搜图 caption 缓存（P95 优化；进程内 LRU + TTL，零依赖） ----
_CAPTION_CACHE_MAX = 256
_CAPTION_CACHE_TTL_SECONDS = 24 * 3600
_caption_cache: dict[str, tuple[float, str]] = {}
_caption_cache_lock = threading.Lock()


def _cached_image_caption(image_path: str) -> str:
    """图片 → caption（按字节 sha256 缓存；重复查询跳过 VL 往返）

    缓存未命中 → 调 image_caption（qwen3-vl-plus，内部含 3 次指数退避重试）；
    最终失败抛异常由调用方降级。**2026-08-29 真实链路加固**：08-28 COCO 实测
    以图搜图 2/10 miss 即 VL 连接重置重试耗尽 → 空结果；现重试耗尽后若存在
    过期缓存（>TTL 旧记录）→ 降级返回旧 caption（网络抖动期检索仍可用），
    无缓存才抛异常。失败兜底不落缓存污染（空 caption 不占缓存位）。
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

    stale = hit[1] if hit else ""  # hit 存在即已过 TTL —— 失败时的兜底素材
    try:
        caption = _vl_caption(image_path).strip()
    except Exception as exc:  # noqa: BLE001 —— VL 最终失败：过期缓存兜底（08-29 加固）
        if stale:
            logger.warning("VL caption 调用失败，降级用过期缓存（%.1fh 前）: %s", (now - hit[0]) / 3600, exc)
            return stale
        raise
    if not caption:
        # VL 成功但空描述（罕见）→ 同样优先旧缓存，避免以图搜图空结果
        return stale
    with _caption_cache_lock:
        if len(_caption_cache) >= _CAPTION_CACHE_MAX:
            # 简单淘汰：清掉最早一半（零依赖，够用）
            for k in sorted(_caption_cache, key=lambda x: _caption_cache[x][0])[: _CAPTION_CACHE_MAX // 2]:
                _caption_cache.pop(k, None)
        _caption_cache[digest] = (now, caption)
    return caption


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

    # 规则级敏感过滤（B5b-1 🟢，与文本搜索同款；Wave1 AgentC 转交）
    if hits:
        try:
            from app.services.external.sensitive_words import filter_sensitive_rule

            hits = [h for h in hits if not (h.text and filter_sensitive_rule(h.text))]
        except Exception:  # noqa: BLE001
            logger.warning("规则级敏感过滤异常，跳过")

    return SearchResult(
        query=q.q,
        rewritten_query=caption or None,
        intent="image",
        hits=hits,
        total=len(hits),
        latency_ms=latency_ms,
        degraded=degraded,
    )
