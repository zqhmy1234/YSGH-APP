"""Frame: nine segments, fixed pruning order, two untouchable segments."""

from datetime import datetime

from agentv3.contracts import Digest, DigestSection, Hit, SearchResult, TurnContext, TurnRequest
from agentv3.frame import FRAME_MAX_CHARS, PERSONA_FLOOR, SEGMENT_CAPS, build_frame
from agentv3.plan import plan


def _plan():
    return plan(TurnContext(request=TurnRequest(person_id="p1", text="上次去杭州"), now=datetime(2026, 11, 23)))


def _build(**over):
    from agentv3.contracts import Digest, DigestSection, Hit, SearchResult

    kwargs = dict(
        plan=_plan(),
        persona="陪" * 1000,
        relation="老" * 200,
        boundaries=["逝者" + "边" * 148, "前任" + "界" * 148],
        corrections=["她叫小林" + "正" * 195, "她住城西" + "修" * 195],
        digest=Digest(person_id="p1", sections=(DigestSection(name="身份", text="x" * 600),)),
        narrative="n" * 300,
        evidence=SearchResult(hits=(Hit(cite_as="S1", evidence_id="e1", kind="content", title="西湖", content="c" * 1590),)),
        crisis_note="危" * 200,
    )
    kwargs.update(over)
    return build_frame(**kwargs)


def test_nine_segments_in_declared_order():
    frame = _build()
    assert [s.name for s in frame.segments] == [
        "persona", "relation", "boundaries", "corrections", "digest", "narrative", "evidence", "task", "crisis",
    ]


def test_total_never_exceeds_budget():
    assert _build().total_chars <= FRAME_MAX_CHARS


def test_evidence_is_pruned_first():
    frame = _build()
    assert "evidence" in frame.dropped


def test_boundaries_and_corrections_survive_pruning():
    frame = _build()
    kept = {s.name: s.text for s in frame.segments}
    assert kept["boundaries"] and kept["corrections"]


def test_persona_floor_is_respected():
    frame = _build()
    persona = next(s for s in frame.segments if s.name == "persona").text
    assert len(persona) >= PERSONA_FLOOR


def test_all_caps_respected():
    frame = _build()
    for segment in frame.segments:
        assert len(segment.text) <= SEGMENT_CAPS[segment.name]
