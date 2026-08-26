"""RAG 检索集成测试（B2：真实 Qdrant + BGE-M3）

前置：Docker Qdrant（yishu-qdrant）+ BGE-M3 模型已下载
运行：pytest backend/tests/test_rag.py -v

覆盖：RET-001（文字搜图占位）/RET-003（dense+sparse 融合）/RET-007（改写）/
      RET-016（溯源）/RET-018（延迟）/API-009（降级）/
      Wave2-F（2026-08-26）：第二层 LLM 精排（B2-1 Ilya 方案）
"""
from __future__ import annotations

import time
import uuid
from datetime import datetime

import pytest
from app.db.models import Content, Event, EventItem, User
from app.db.session import SessionLocal
from app.schemas.search import SearchQuery
from app.services.embedding import encode_dense, encode_query
from app.services.rag import _rewrite_query, _route_query, search
from app.services.vector_store import get_store
from qdrant_client.http import models  # noqa: F401
from sqlalchemy import delete as sa_delete

# 测试专用隔离 collection（2026-08-25 修复：原与生产 yishu_contents 共用，
# 生产库有真实数据后测试点被挤出 Top-k 导致 flaky——与基准评测同样隔离）
TEST_COLLECTION = "yishu_test_rag"

# R8#14（2026-08-27）：测试语料按 payload 标记删除（不再用固定 id 枚举删除）——
# 语料扩充后旧点不再残留；仅在本测试隔离 collection（yishu_test_rag）内安全。
BENCH_MARK = "rag-distribution"

# 测试语料（中文记忆场景）
CORPUS = [
    {
        "id": "rag-001",
        "text": "去年夏天和爸妈去了杭州西湖，坐了手摇船，拍了荷花",
        "tags": ["旅行", "家庭"],
        "taken_at": "2025-07-15T10:00:00+08:00",
    },
    {
        "id": "rag-002",
        "text": "考研备考第 200 天，数学真题刷完两遍",
        "tags": ["备考"],
        "taken_at": "2026-05-20T09:00:00+08:00",
    },
    {
        "id": "rag-003",
        "text": "和朋友在苏州平江路吃松鼠桂鱼，人均 80",
        "tags": ["美食"],
        "taken_at": "2025-10-01T12:30:00+08:00",
    },
    {
        "id": "rag-004",
        "text": "今天加班到十点，项目终于上线了",
        "tags": ["工作"],
        "taken_at": "2026-08-10T22:00:00+08:00",
    },
    {
        "id": "rag-005",
        "text": "猫主子第一次打疫苗，全程很乖",
        "tags": ["宠物"],
        "taken_at": "2026-03-08T15:00:00+08:00",
    },
]


def _ts(s: str) -> int:
    """ISO 时间串 → epoch 秒（审查 CRITICAL 修复：payload 侧 taken_at 与 _to_filter 的 Range 数值一致）"""
    return int(datetime.fromisoformat(s).timestamp())


@pytest.fixture(scope="module")
def indexed_store():
    """建索引：语料写入 Qdrant（幂等：先按 payload 标记清理再写）

    审查 CRITICAL 修复：payload 的 taken_at 存 epoch 秒（int），
    与 _to_filter 的 Range(gte/lte=value.timestamp()) 数值过滤一致——
    此前存 ISO 字符串导致时间过滤静默不命中（恒空结果）。
    R8#14（2026-08-27）：固定 id 枚举删除改按 payload 标记删除
    （语料扩充不残留旧点）；R8#9：固定 sleep 改轮询等索引最终一致性。
    """
    store = get_store()
    store.ensure_collection(TEST_COLLECTION)
    # 清理旧测试点：删除全部 benchmark 标记点（含扩充后的旧语料 + fix1-* 回归点）
    store.client.delete(
        collection_name=TEST_COLLECTION,
        points_selector=models.FilterSelector(
            filter=models.Filter(
                must=[
                    models.FieldCondition(
                        key="benchmark",
                        match=models.MatchValue(value=BENCH_MARK),
                    )
                ]
            )
        ),
    )
    for doc in CORPUS:
        dense, sparse = encode_query(doc["text"])
        store.upsert_content(
            content_id=doc["id"],
            text=doc["text"],
            dense=dense,
            sparse=sparse,
            payload={
                "content_type": "text",
                "tags": doc["tags"],
                "taken_at": _ts(doc["taken_at"]),
                "benchmark": BENCH_MARK,
            },
            collection=TEST_COLLECTION,
        )
    # R8#14：索引最终一致性（R8#9 批次将改轮询，见后续提交）
    time.sleep(0.5)
    return store


@pytest.mark.rag
def test_embedding_dimension():
    """BGE-M3 dense 维度 = 1024（B2 named vectors 一致性）"""
    dense = encode_dense(["测试"])[0]
    assert len(dense) == 1024


@pytest.mark.rag
@pytest.mark.integration
def test_dense_search_recall(indexed_store):
    """dense 召回：语义相似命中（RET-003 前置）"""
    q = SearchQuery(q="杭州旅行荷花")
    result = search(q, collection=TEST_COLLECTION)
    ids = [h.content_id for h in result.hits]
    assert "rag-001" in ids, f"语义检索应命中 rag-001, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_sparse_keyword_recall(indexed_store):
    """sparse 召回：关键词精确命中（错别字/口语，RET-004）"""
    q = SearchQuery(q="松鼠桂鱼")
    result = search(q, collection=TEST_COLLECTION)
    ids = [h.content_id for h in result.hits]
    assert "rag-003" in ids, f"关键词检索应命中 rag-003, got {ids}"


@pytest.mark.rag
@pytest.mark.integration
def test_query_rewrite_time_filter():
    """Query 改写：'去年' → 时间过滤条件（RET-007）"""
    q = SearchQuery(q="去年去的地方")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "去的地方"
    assert filters.get("time_from") is not None
    assert filters["time_from"].year == 2025
    assert ner_filters == {}


def test_query_rewrite_time_modifier_mid_query():
    """2026-08-25 RAG 审查修复：句中时间词是名词修饰语，不是过滤意图

    回归："记得明天下午之前把上个月的工作总结报告交给领导" 此前误加 time 过滤
    → 语料无 taken_at 时检索空结果（benchmark length 层 hit_rate 0.5 根因）。
    现在句中"上个月"不再触发过滤，也不删词。
    """
    q = SearchQuery(q="记得明天下午之前把上个月的工作总结报告交给领导")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert "time_from" not in filters
    assert "time_to" not in filters
    assert rewritten == q.q
    assert ner_filters == {}


def test_boost_exact_matches():
    """2026-08-25 RAG 审查新增：词元全命中文档提升到稠密噪声之上

    用贴近 RRF 的密集分数（0.0115 vs 0.0111，真实 RRF 分数差距极小）——
    ×1.8 提升足够把精确命中文档顶到首位。
    """
    from app.services.rag import _boost_exact_matches

    hits = [
        {"content_id": "a", "text": "今天跑了马拉松，配速五分", "score": 0.0111},
        {"content_id": "b", "text": "周末计划和朋友吃饭", "score": 0.0115},
    ]
    boosted = _boost_exact_matches("马拉松", hits)
    assert boosted[0]["content_id"] == "a"
    assert boosted[0]["score"] > boosted[1]["score"]
    # 描述性查询无全词命中 → 原序
    hits2 = [dict(h) for h in hits]
    boosted2 = _boost_exact_matches("关于做产品的想法", hits2)
    assert [h["content_id"] for h in boosted2] == ["b", "a"]


def test_rewrite_ner_place_extraction():
    """B2-2 NER：查询含地名 → place 过滤 + ner_filters 标记（可回退）"""
    q = SearchQuery(q="苏州的美食")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert filters.get("place") == "苏州"
    assert ner_filters.get("place") == "苏州"


def test_rewrite_ner_explicit_param_wins():
    """显式 place 参数优先于 NER 抽取（不标记为 NER 派生，硬约束）"""
    q = SearchQuery(q="苏州的美食", place="上海")
    _, filters, ner_filters = _rewrite_query(q)
    assert filters.get("place") == "上海"
    assert "place" not in ner_filters


def test_route_query():
    """查询路由：图片意图识别（B2 路由）"""
    assert _route_query("照片里的猫") == "image"
    assert _route_query("考研笔记") == "text"


@pytest.mark.rag
@pytest.mark.integration
def test_search_trace_present(indexed_store):
    """溯源：每条命中带 dense/sparse 分数解释（RET-016）"""
    q = SearchQuery(q="猫")
    result = search(q, collection=TEST_COLLECTION)
    if result.hits:
        for h in result.hits:
            assert h.trace, "必须带溯源"
            assert "dense_score" in h.trace or "sparse_score" in h.trace


@pytest.mark.rag
@pytest.mark.integration
def test_search_latency_under_3s(indexed_store):
    """延迟：P95<3s（RET-018，M1 门禁）"""
    latencies = []
    for i in range(5):
        start = time.perf_counter()
        search(SearchQuery(q=f"测试查询第{i}条 杭州"), collection=TEST_COLLECTION)
        latencies.append((time.perf_counter() - start) * 1000)
    p95 = sorted(latencies)[3]
    assert p95 < 3000, f"P95={p95:.0f}ms 超预算"


@pytest.mark.rag
@pytest.mark.integration
def test_time_filter_actually_filters(indexed_store):
    """审查 CRITICAL 修复：时间过滤必须真实生效（payload taken_at 为 epoch 秒）

    此前 payload 存 ISO 字符串而 _to_filter 用数值 Range → Qdrant 类型不匹配
    静默不命中（恒空结果）。本测试验证：带时间过滤检索能命中对应语料。
    """
    from datetime import datetime, timezone

    store = indexed_store
    dense, sparse = encode_query("回忆")
    # 2026 年时间窗（语料中 rag-002/004/005 落在 2026 年）
    lo = datetime(2026, 1, 1, tzinfo=timezone.utc)
    hi = datetime(2026, 12, 31, tzinfo=timezone.utc)
    hits_2026 = store.search(
        dense, sparse,
        filters={"time_from": lo, "time_to": hi},
        limit=50,
        collection=TEST_COLLECTION,
    )
    ids_2026 = {h["content_id"] for h in hits_2026}
    assert ids_2026, "2026 年时间窗不应为空（修复前恒空）"
    assert not any(pid == "rag-001" for pid in ids_2026), "2025 年语料不应出现在 2026 时间窗"
    # 反向：2025 年时间窗应命中 rag-001/003
    lo25 = datetime(2025, 1, 1, tzinfo=timezone.utc)
    hi25 = datetime(2025, 12, 31, tzinfo=timezone.utc)
    hits_2025 = store.search(
        dense, sparse,
        filters={"time_from": lo25, "time_to": hi25},
        limit=50,
        collection=TEST_COLLECTION,
    )
    ids_2025 = {h["content_id"] for h in hits_2025}
    assert "rag-001" in ids_2025
    assert "rag-003" in ids_2025


def test_search_empty_query_rejected():
    """空查询 → 校验层拒绝（路由层不处理）"""
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        SearchQuery(q="")

def test_boost_exact_matches_partial():
    """P0-D（2026-08-25）：部分词元命中梯度 —— ≥50% 词元命中 ×1.3

    全命中仍 ×1.8；3 词元中 2 个命中 → ×1.3；1/4 命中 → 不提升。
    （中文无空格，词元按标点切分——查询用标点分隔多词元。）
    """
    from app.services.rag import _boost_exact_matches

    hits = [
        {"content_id": "a", "text": "季度总结报告", "score": 0.0111},
        {"content_id": "b", "text": "提交平台", "score": 0.0115},
        {"content_id": "c", "text": "完全无关内容", "score": 0.0110},
    ]
    # 3 词元（季度/总结/提交）：a 命中 2/3 ≥50% → ×1.3 顶到首位；b 1/3 → 不提升
    boosted = _boost_exact_matches("季度、总结、提交", hits)
    assert boosted[0]["content_id"] == "a"
    assert boosted[0]["score"] == round(0.0111 * 1.3, 4)
    # 4 词元：b 只命中 1/4 <50% → 原分不动
    boosted2 = _boost_exact_matches("季度、总结、提交、审核", hits)
    b = next(h for h in boosted2 if h["content_id"] == "b")
    assert b["score"] == 0.0115
    # 全命中仍 ×1.8（单 token 查询不受影响）
    hits3 = [{"content_id": "c", "text": "买牛奶和鸡蛋", "score": 0.01}]
    boosted3 = _boost_exact_matches("买牛奶", hits3)
    assert boosted3[0]["score"] == round(0.01 * 1.8, 4)


def test_classify_query_intent_descriptive():
    """P1-A（2026-08-25）：描述性查询 → 规则词表主导类别（修复 descriptive 召回缺口）"""
    from app.services.rag import _classify_query_intent

    assert _classify_query_intent("关于做产品的想法") == "idea"
    assert _classify_query_intent("让我难过的记录") == "emotion"
    assert _classify_query_intent("记得要去办的事情") == "todo"
    assert _classify_query_intent("人生感悟和道理") == "quote"
    assert _classify_query_intent("买牛奶") == "todo"
    assert _classify_query_intent("收房租") == "todo"
    assert _classify_query_intent("买牛乃") == "todo"  # 错字不阻断（"买"命中）


def test_classify_query_intent_no_class():
    """P1-A：无主导类别/并列 → None（不过滤，防误过滤）"""
    from app.services.rag import _classify_query_intent

    # 无词表命中：关键词/实体查询不过滤（马拉松靠语义召回）
    assert _classify_query_intent("马拉松") is None
    assert _classify_query_intent("松鼠桂鱼") is None
    assert _classify_query_intent("") is None
    # 并列命中（两类各 1 次）→ None
    assert _classify_query_intent("难过的事情记得处理") is None


# ---- audit #17：时间词表扩充（2026-08-26）----


def test_query_rewrite_time_last_summer():
    """'去年夏天' → 去年 6/1 - 8/31 窗口（长模式优先于 '去年'）"""
    q = SearchQuery(q="去年夏天去的地方")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "去的地方"
    assert filters["time_from"].month == 6 and filters["time_from"].day == 1
    assert filters["time_from"].year == 2025
    assert filters["time_to"].month == 8 and filters["time_to"].day == 31
    assert ner_filters == {}


def test_query_rewrite_time_two_weeks_ago():
    """'上上周' → 前 14 天 00:00 → 前 7 天 23:59:59"""
    q = SearchQuery(q="上上周的旅行")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "的旅行"
    assert filters["time_from"] is not None and filters["time_to"] is not None
    span = (filters["time_to"] - filters["time_from"]).days
    assert 6 <= span <= 7, f"上上周窗口应为 ~7 天, got {span}"
    assert filters["time_from"].hour == 0 and filters["time_to"].hour == 23
    assert ner_filters == {}


def test_query_rewrite_time_three_years_ago():
    """'三年前' → 三年前自然年窗口"""
    q = SearchQuery(q="三年前的今天")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "的今天"
    assert filters["time_from"].year == 2023
    assert filters["time_from"].month == 1 and filters["time_from"].day == 1
    assert filters["time_to"].year == 2023 and filters["time_to"].month == 12
    assert ner_filters == {}


def test_query_rewrite_time_year_before_last():
    """'前年' → 前年（now.year - 2）自然年窗口"""
    q = SearchQuery(q="前年去过的地方")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "去过的地方"
    assert filters["time_from"].year == 2024
    assert filters["time_to"].year == 2024
    assert ner_filters == {}


def test_query_rewrite_time_last_week():
    """'上周' → 前 7 天 → 前 1 天窗口"""
    q = SearchQuery(q="上周的会议")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "的会议"
    span = (filters["time_to"] - filters["time_from"]).days
    assert 6 <= span <= 7
    assert ner_filters == {}


def test_query_rewrite_time_day_before_yesterday():
    """'前天' → 前 2 天整天窗口"""
    q = SearchQuery(q="前天拍的照片")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert rewritten == "拍的照片"
    assert filters["time_from"].hour == 0 and filters["time_to"].hour == 23
    span = (filters["time_to"] - filters["time_from"]).days
    assert span == 0
    assert ner_filters == {}


def test_query_rewrite_new_patterns_mid_query_not_filter():
    """新词表中句："去年夏天/上上周" 作名词修饰语 → 不触发时间过滤（与 '上个月' 同语义）"""
    q = SearchQuery(q="我们去年夏天去了苏州")
    rewritten, filters, ner_filters = _rewrite_query(q)
    assert "time_from" not in filters and "time_to" not in filters
    assert rewritten == q.q
    q2 = SearchQuery(q="记得上上周的总结还没交")
    _, filters2, _ = _rewrite_query(q2)
    assert "time_from" not in filters2


# ---- FIX-1（audit #5）：photo 点可被图片意图命中（回归）----


@pytest.mark.rag
@pytest.mark.integration
@pytest.mark.usefixtures("indexed_store")
def test_image_intent_hits_photo_points():
    """FIX-1 回归：生产 photo 点（payload content_type="photo"）必须能被图片意图命中

    此前检索过滤用 "image" 而生产 payload 为 "photo" → 生产文字搜图/以图搜图
    恒空结果（评测集用 "image" 索引故未暴露）。本测试验证：
    1) image 意图（"照片里的猫"）→ 命中 content_type="photo" 的点；
    2) 遗留 "image" 点同样命中（过滤端 MatchAny 兼容）；
    3) text 点不被 image 意图过滤命中。
    """
    store = get_store()
    docs = [
        {"id": "fix1-photo", "text": "这张照片里有一只猫趴在窗台上", "ct": "photo"},
        {"id": "fix1-legacy", "text": "截图：课程表安排", "ct": "image"},
        {"id": "fix1-text", "text": "记录今天跑步五公里", "ct": "text"},
    ]
    for d in docs:
        dense, sparse = encode_query(d["text"])
        store.upsert_content(
            content_id=d["id"], text=d["text"], dense=dense, sparse=sparse,
            payload={"content_type": d["ct"], "text": d["text"], "benchmark": BENCH_MARK},
            collection=TEST_COLLECTION,
        )
    # R8#14：索引最终一致性（R8#9 批次将改轮询，见后续提交）
    time.sleep(0.5)

    # image 意图：应命中 photo 点（生产语义），不命中 text 点
    r = search(SearchQuery(q="照片里的猫", limit=20), collection=TEST_COLLECTION)
    assert r.intent == "image"
    ids = [h.content_id for h in r.hits]
    assert "fix1-photo" in ids, f"image 意图应命中 photo 点, got {ids}"
    assert "fix1-text" not in ids, "image 意图不应命中 text 点"

    # 显式 content_types=["photo"]：遗留 "image" 点也命中（旧数据不丢）
    r2 = search(SearchQuery(q="课程表", content_types=["photo"], limit=20), collection=TEST_COLLECTION)
    ids2 = [h.content_id for h in r2.hits]
    assert "fix1-legacy" in ids2, f"遗留 image 点应被 photo 过滤命中, got {ids2}"
    assert "fix1-text" not in ids2


# ---- audit #16：Qdrant 降级 PG 全文检索兜底 ----


@pytest.fixture()
def pg_db_user():
    """PG 会话 + 用户（测试隔离：清理本测试创建的数据）"""
    db = SessionLocal()
    user = User(phone=f"ragpg-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(EventItem))
    db.execute(sa_delete(Event).where(Event.user_id == user.id))
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.delete(user)
    db.commit()
    db.close()


@pytest.mark.integration
def test_pg_fallback_search_returns_hits(pg_db_user):
    """Qdrant 降级 → PG ILIKE 兜底：命中真实内容 + pg 通道标记 + 用户隔离"""
    from app.services.rag import _pg_fallback_search

    db, user = pg_db_user
    c_match = Content(
        user_id=user.id, content_type="text", text="杭州旅行西湖边散步", status="done", source="app"
    )
    c_other = Content(
        user_id=user.id, content_type="text", text="完全无关的笔记", status="done", source="app"
    )
    db.add_all([c_match, c_other])
    db.commit()

    hits = _pg_fallback_search(
        SearchQuery(q="杭州旅行"), "杭州旅行",
        {"user_id": str(user.id)}, db, str(user.id), 10,
    )
    ids = [h["content_id"] for h in hits]
    assert str(c_match.id) in ids, f"PG 兜底应命中匹配内容, got {ids}"
    assert str(c_other.id) not in ids
    assert hits[0]["pg"] is True
    assert hits[0]["dense_score"] == 0.0


@pytest.mark.integration
def test_pg_fallback_search_filters_content_type(pg_db_user):
    """PG 兜底遵守 content_type 过滤（FIX-1 同口径：photo 过滤只匹配 photo 内容）"""
    from app.services.rag import _pg_fallback_search

    db, user = pg_db_user
    c_photo = Content(
        user_id=user.id, content_type="photo", text="杭州西湖照片记录", status="done", source="app"
    )
    c_text = Content(
        user_id=user.id, content_type="text", text="杭州西湖文字笔记", status="done", source="app"
    )
    db.add_all([c_photo, c_text])
    db.commit()

    hits = _pg_fallback_search(
        SearchQuery(q="杭州西湖"), "杭州西湖",
        {"user_id": str(user.id), "content_types": ["photo"]}, db, str(user.id), 10,
    )
    ids = [h["content_id"] for h in hits]
    assert str(c_photo.id) in ids
    assert str(c_text.id) not in ids, "photo 过滤不应命中 text 内容"

    # 遗留 "image" 请求值 → 归一为 photo（别名兼容）
    hits2 = _pg_fallback_search(
        SearchQuery(q="杭州西湖"), "杭州西湖",
        {"user_id": str(user.id), "content_types": ["image"]}, db, str(user.id), 10,
    )
    ids2 = [h["content_id"] for h in hits2]
    assert str(c_photo.id) in ids2


# ---- audit #15：溯源事件级归因 ----


@pytest.mark.integration
def test_assemble_hits_event_attribution(pg_db_user):
    """事件级归因：content 关联事件后，SearchHit 回填 event_id/event_title"""
    from app.services.rag import _assemble_hits

    db, user = pg_db_user
    c = Content(
        user_id=user.id, content_type="text", text="杭州旅行记录", status="done", source="app"
    )
    db.add(c)
    db.commit()
    ev = Event(user_id=user.id, level=1, title="杭州之行", status="confirmed", generated_by="cloud")
    db.add(ev)
    db.commit()
    db.add(EventItem(content_id=c.id, event_id=ev.id))
    db.commit()

    hits = _assemble_hits(
        [{"content_id": str(c.id), "score": 0.9, "dense_score": 0.9, "sparse_score": 0.0, "text": c.text}],
        10, db, str(user.id),
    )
    assert len(hits) == 1
    assert hits[0].event_id == str(ev.id)
    assert hits[0].event_title == "杭州之行"
    assert hits[0].content_type == "text"


@pytest.mark.integration
def test_assemble_hits_event_attribution_user_isolation(pg_db_user):
    """事件归因按用户隔离：他人事件不回填"""
    from app.services.rag import _assemble_hits

    db, user = pg_db_user
    c = Content(
        user_id=user.id, content_type="text", text="杭州旅行记录", status="done", source="app"
    )
    db.add(c)
    db.commit()
    # 他人事件关联到本用户内容 → 不应回填（Event.user_id 隔离）
    other = User(phone=f"ragoth-{uuid.uuid4().hex[:8]}", status=1)
    db.add(other)
    db.commit()
    ev = Event(user_id=other.id, level=1, title="他人事件", status="confirmed", generated_by="cloud")
    db.add(ev)
    db.commit()
    db.add(EventItem(content_id=c.id, event_id=ev.id))
    db.commit()

    hits = _assemble_hits(
        [{"content_id": str(c.id), "score": 0.9, "dense_score": 0.9, "sparse_score": 0.0, "text": c.text}],
        10, db, str(user.id),
    )
    assert hits[0].event_id is None
    assert hits[0].event_title is None

    # 清理他人用户（避免污染后续测试）
    db.execute(sa_delete(EventItem).where(EventItem.event_id == ev.id))
    db.execute(sa_delete(Event).where(Event.id == ev.id))
    db.delete(other)
    db.commit()


# ---- Wave2-F（2026-08-26）：第二层 LLM 精排（B2-1 Ilya 方案）----


def test_llm_rerank_mock_returns_original_order(monkeypatch):
    """无 key / mock 模式：LLM 精排原序返回（RRF 分保底），不改候选集、不抛错"""
    from app.services.llm_ops.rerank import llm_rerank

    hits = [
        {"id": "a", "text": "杭州旅行记录", "score": 0.9},
        {"id": "b", "text": "苏州美食记录", "score": 0.8},
        {"id": "c", "text": "考研备考记录", "score": 0.7},
    ]
    out = llm_rerank("杭州旅行", hits, top_k=2)
    assert [h["id"] for h in out] == ["a", "b", "c"]
    assert all("rerank_reason" not in h for h in out)
    assert all("rerank_rank" not in h for h in out)


def test_llm_rerank_disabled_returns_original(monkeypatch):
    """开关关闭：即使 LLM 可用也原序（配置门控）"""
    from app.core.config import settings
    from app.services.llm_ops.rerank import llm_rerank

    monkeypatch.setattr(settings, "rerank_llm_enabled", False)
    hits = [{"id": "a", "text": "x", "score": 0.5}, {"id": "b", "text": "y", "score": 0.4}]
    out = llm_rerank("q", hits)
    assert [h["id"] for h in out] == ["a", "b"]


def test_llm_rerank_judged_ordering_and_reason(monkeypatch):
    """真实判定路径：能回答组（原分降序）在前 + 不能回答组在后；top_k 记名次 + 理由"""
    from app.services.llm_ops import rerank as rerank_mod

    hits = [
        {"id": "a", "text": "杭州西湖手摇船荷花", "score": 0.9},
        {"id": "b", "text": "苏州松鼠桂鱼人均八十", "score": 0.8},
        {"id": "c", "text": "马拉松五公里痛快", "score": 0.7},
    ]
    monkeypatch.setattr(rerank_mod, "llm_available", lambda: True)
    monkeypatch.setattr(
        rerank_mod,
        "chat_text",
        lambda system, user: (
            '```json\n['
            '{"i":0,"ans":true,"reason":"直接命中杭州"},'
            '{"i":2,"ans":true,"reason":"内容相关"},'
            '{"i":1,"ans":false,"reason":"无关"}'
            ']\n```'
        ),
    )
    out = rerank_mod.llm_rerank("杭州旅行", hits, top_k=2)
    assert [h["id"] for h in out] == ["a", "c", "b"]
    assert out[0]["rerank_rank"] == 1
    assert out[1]["rerank_rank"] == 2
    assert "命中" in out[0]["rerank_reason"]
    assert out[2]["rerank_reason"] == "无关"


def test_llm_rerank_parse_failure_returns_original(monkeypatch):
    """LLM 输出无法解析：原序返回（降级不吞结果）"""
    from app.services.llm_ops import rerank as rerank_mod

    hits = [{"id": "a", "text": "x", "score": 0.5}, {"id": "b", "text": "y", "score": 0.4}]
    monkeypatch.setattr(rerank_mod, "llm_available", lambda: True)
    monkeypatch.setattr(rerank_mod, "chat_text", lambda system, user: "完全不是 JSON")
    out = rerank_mod.llm_rerank("q", hits)
    assert [h["id"] for h in out] == ["a", "b"]


def test_llm_rerank_exception_returns_original(monkeypatch):
    """LLM 调用抛异常：原序返回（不向搜索链路抛错）"""
    from app.services.llm_ops import rerank as rerank_mod

    hits = [{"id": "a", "text": "x", "score": 0.5}]
    monkeypatch.setattr(rerank_mod, "llm_available", lambda: True)
    monkeypatch.setattr(
        rerank_mod, "chat_text", lambda system, user: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    out = rerank_mod.llm_rerank("q", hits)
    assert [h["id"] for h in out] == ["a"]


def test_rerank_auto_enabled_strategy(monkeypatch):
    """第一层 reranker 自动启用策略：显式优先 / 无 GPU 保持关 / 开关默认关"""
    from app.core.config import settings
    from app.services.rerank import rerank_auto_enabled

    monkeypatch.setattr(settings, "rerank_auto_enable", False)
    monkeypatch.setattr(settings, "rerank_enabled", False)
    assert rerank_auto_enabled() is False

    # 显式 rerank_enabled=True → 生效（忽略 auto 开关）
    monkeypatch.setattr(settings, "rerank_enabled", True)
    assert rerank_auto_enabled() is True

    # auto=True 但无 GPU → 关闭
    monkeypatch.setattr(settings, "rerank_enabled", False)
    monkeypatch.setattr(settings, "rerank_auto_enable", True)
    from app.services import rerank as rerank_mod

    monkeypatch.setattr(rerank_mod, "_gpu_available", lambda: False)
    assert rerank_auto_enabled() is False


@pytest.mark.rag
@pytest.mark.integration
def test_search_llm_rerank_wiring_mock_noop(indexed_store):
    """rag.py 接线回归：mock 模式下 LLM 精排为 no-op，搜索行为与精排前一致

    - 结果数量/排序不被破坏（judged 为空 → 不替换 hits）
    - 全链路不抛错；mock 时 trace 不带 llm_rerank_reason
    """
    q = SearchQuery(q="杭州旅行荷花", limit=5)
    result = search(q, collection=TEST_COLLECTION)
    ids = [h.content_id for h in result.hits]
    assert "rag-001" in ids
    assert all("llm_rerank_reason" not in (h.trace or {}) for h in result.hits)


@pytest.mark.rag
@pytest.mark.integration
def test_search_llm_rerank_wiring_judged(indexed_store, monkeypatch):
    """rag.py 接线：LLM 判定生效时 trace 回填精排理由 + 排序被精排接管"""
    from app.services.llm_ops import rerank as rerank_mod

    monkeypatch.setattr(rerank_mod, "llm_available", lambda: True)
    monkeypatch.setattr(
        rerank_mod,
        "chat_text",
        lambda system, user: (
            '[{"i":0,"ans":true,"reason":"杭州荷花命中"},'
            '{"i":1,"ans":false,"reason":"无关"},'
            '{"i":2,"ans":true,"reason":"相关"}]'
        ),
    )
    q = SearchQuery(q="杭州旅行荷花", limit=5)
    result = search(q, collection=TEST_COLLECTION)
    assert result.hits
    # 至少一条 trace 回填了 llm_rerank_reason（被精排判定过）
    assert any("llm_rerank_reason" in (h.trace or {}) for h in result.hits)
