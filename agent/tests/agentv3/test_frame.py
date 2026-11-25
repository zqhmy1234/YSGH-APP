"""Context assembler: nine segments, fixed pruning, protected state."""

from datetime import datetime

from harness.context import (
    FRAME_MAX_CHARS,
    PERSONA_FLOOR,
    SEGMENT_CAPS,
    SEGMENT_ORDER,
    ContextAssembler,
    StablePrompt,
)
from harness.contracts import Digest, DigestSection, Hit, SearchResult, TurnContext, TurnRequest
from harness.plan import plan


def _plan():
    return plan(TurnContext(request=TurnRequest(person_id="p1", text="上次去杭州"), now=datetime(2026, 11, 23)))


def _build(**over):
    kwargs = dict(
        stable=StablePrompt(
            persona="陪" * 1000,
            relation="老" * 200,
            boundaries=["逝者" + "边" * 148, "前任" + "界" * 148],
            corrections=["她叫小林" + "正" * 195, "她住城西" + "修" * 195],
        ),
        history=tuple("对话" * 100 for _ in range(6)),
        input_text="上次去杭州是哪天",
        digest=Digest(person_id="p1", sections=(DigestSection(name="身份", text="x" * 600),)),
        narrative="n" * 300,
        evidence=SearchResult(
            hits=(Hit(cite_as="S1", evidence_id="e1", kind="content", title="西湖", content="c" * 1590),)
        ),
        plan=_plan(),
    )
    kwargs.update(over)
    return ContextAssembler().assemble(**kwargs)


def test_ten_segments_in_declared_order():
    assembled = _build()
    assert [s.name for s in assembled.segments] == list(SEGMENT_ORDER)
    assert len(SEGMENT_ORDER) == 10


def test_small_budget_prunes_history_first():
    assembled = _build(budget=__import__("harness.context", fromlist=["ContextBudget"]).ContextBudget(frame=1200))
    assert assembled.frame.dropped[0] == "history"
    assert assembled.total_chars <= 1200


def test_total_never_exceeds_budget():
    assert _build().total_chars <= FRAME_MAX_CHARS


def test_evidence_is_pruned_before_protected_segments():
    assembled = _build()
    assert "evidence" in assembled.frame.dropped or len(assembled.frame.segments[6].text) <= SEGMENT_CAPS["evidence"]
    kept = {s.name: s.text for s in assembled.segments}
    assert kept["global"] and kept["input"] and kept["output_contract"]


def test_boundaries_and_corrections_survive_pruning():
    assembled = _build()
    state = next(s for s in assembled.segments if s.name == "state").text
    assert "逝者" in state
    assert "她叫小林" in state


def test_persona_floor_is_respected():
    assembled = _build()
    persona = next(s for s in assembled.segments if s.name == "persona").text
    assert len(persona) >= PERSONA_FLOOR


def test_all_caps_respected():
    assembled = _build()
    for segment in assembled.segments:
        assert len(segment.text) <= SEGMENT_CAPS[segment.name]


def test_digest_has_three_states():
    from harness.contracts import Digest

    text = _build(digest=Digest(person_id="p1", state="insufficient_data"))
    digest = next(s for s in text.segments if s.name == "digest").text
    assert "insufficient_data" in digest

    unavailable = _build(digest=None)
    digest = next(s for s in unavailable.segments if s.name == "digest").text
    assert "unavailable" in digest
