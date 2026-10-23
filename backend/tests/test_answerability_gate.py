"""answerability 拒答闸门（2026-10-23）

纯单元测试：不碰 Qdrant / 不调 LLM。
覆盖《RAG 检索链路与准确率提升方案_20261018》§三 P0-2 的拒答语义：

- 全部明确判「不能回答」→ 空（系统会说出「我没有这条记忆」）；
- 只要一条能回答 → 原样返回（不做逐条剔除，保 hit_rate@3）；
- 没有明确判定（mock / 无 key / 解析失败）→ 原样返回（fail-open，不能把结果清空）；
- 开关默认关闭（生产行为不变）。
"""
from __future__ import annotations

from app.schemas.search import SearchHit
from app.services.rag import _apply_answerability_gate


def _hit(cid: str, answerable: bool | None = ..., trace_has_key: bool = True) -> SearchHit:
    trace: dict = {"dense_score": 0.5}
    if trace_has_key:
        trace["llm_answerable"] = answerable
    return SearchHit(
        content_id=cid,
        content_type="text",
        text=f"文本 {cid}",
        taken_at=None,
        place=None,
        event_id=None,
        event_title=None,
        score=0.5,
        trace=trace,
    )


def test_gate_rejects_when_all_judged_false():
    hits = [_hit("a", False), _hit("b", False)]
    assert _apply_answerability_gate(hits) == []


def test_gate_keeps_all_when_any_judged_true():
    hits = [_hit("a", False), _hit("b", True), _hit("c", False)]
    assert [h.content_id for h in _apply_answerability_gate(hits)] == ["a", "b", "c"]


def test_gate_fail_open_when_nothing_judged():
    """mock / 无 key / 解析失败 → trace 里没有判定（None）→ 不能清空结果"""
    hits = [_hit("a", None), _hit("b", None)]
    assert [h.content_id for h in _apply_answerability_gate(hits)] == ["a", "b"]


def test_gate_fail_open_when_trace_key_absent():
    """旧路径/未经过精排的命中没有 llm_answerable 键 → 原样返回"""
    hits = [_hit("a", None, trace_has_key=False), _hit("b", None, trace_has_key=False)]
    assert [h.content_id for h in _apply_answerability_gate(hits)] == ["a", "b"]


def test_gate_mixed_judged_and_unjudged_keeps_all():
    """部分候选未判定（如精排输出截断）→ 只要有一条判能回答就保留；全 false 才拒答"""
    assert [h.content_id for h in _apply_answerability_gate([_hit("a", True), _hit("b", None)])] == ["a", "b"]
    assert _apply_answerability_gate([_hit("a", False), _hit("b", None)]) == []


def test_gate_empty_hits():
    assert _apply_answerability_gate([]) == []


def test_answerability_reject_defaults_off():
    """默认关闭：不验收前生产行为必须与改动前一致（显式开启才生效）"""
    from app.core.config import settings

    assert settings.answerability_reject_enabled is False
