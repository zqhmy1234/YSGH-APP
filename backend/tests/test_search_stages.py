"""分阶段打点契约测试（2026-10-16 检索超时治理 · 立项①）

打点口径（`rag/__init__.py:_search_impl`）：lap 串行切片，
rewrite+encode+recall 的止点与 `latency_ms` 快照同点 ⇒ 三段之和 ≈ latency_ms。
"""
import pytest
from app.schemas.search import SearchQuery
from app.services.rag import search as rag_search

STAGE_KEYS = {"rewrite", "encode", "recall", "rerank", "answer"}


def test_search_stages_default_not_returned(db_user):
    """默认 want_stages=False ⇒ stages 为 None（在野客户端零影响，响应与改动前一致）"""
    db, user = db_user
    result = rag_search(SearchQuery(q="吃饭", limit=5), db=db, user_id=str(user.id))
    assert result.stages is None


def test_search_stages_breakdown_when_requested(db_user):
    """want_stages=True ⇒ 五键齐全；已执行阶段为非负 int，未执行的 answer 如实 None"""
    db, user = db_user
    result = rag_search(
        SearchQuery(q="吃饭", limit=5, want_stages=True), db=db, user_id=str(user.id)
    )
    stages = result.stages
    assert stages is not None
    assert set(stages.keys()) == STAGE_KEYS
    for key in ("rewrite", "encode", "recall", "rerank"):
        value = stages[key]
        assert isinstance(value, int), f"stages[{key}] 应为 int, got {type(value)}"
        assert value >= 0, f"stages[{key}] 不应为负: {value}"
    assert stages["answer"] is None  # want_answer=False ⇒ 该段未执行，如实 null 不编造
    # 口径守卫：rewrite+encode+recall 与 latency_ms 同起止点，容差 200ms 防调度抖动
    assert stages["rewrite"] + stages["encode"] + stages["recall"] <= result.latency_ms + 200


@pytest.mark.parametrize("want_answer", [True, False])
def test_search_stages_answer_stage_only_when_requested(db_user, want_answer):
    """answer 段计时只在 want_answer=True 时有值；开回答时五段齐全且非负"""
    db, user = db_user
    result = rag_search(
        SearchQuery(q="吃饭", limit=5, want_stages=True, want_answer=want_answer),
        db=db,
        user_id=str(user.id),
    )
    stages = result.stages
    assert stages is not None
    if want_answer:
        assert isinstance(stages["answer"], int) and stages["answer"] >= 0
    else:
        assert stages["answer"] is None
