"""Librarian: RRF, alternate limit, two ranked lists, honest empty/partial."""

from datetime import date

from adapters.fakes import FakeEvents, FakeSearch, _hit
from agents.librarian import RRF_K, Librarian
from harness.contracts import EventHit, ResultStatus


def _lib(corpus=None, down=(), events=None):
    search = FakeSearch(corpus, down=down)
    return Librarian(search, events), search


def test_resolve_query_trims_alternates_to_six_and_dedupes():
    lib, _ = _lib()
    query = lib.resolve_query(
        "那次旅行那天", "p1", alternates=("x" * 199, "x" * 199, *[f"a{i}" for i in range(8)], "那次旅行那天")
    )
    assert len(query.alternates) <= 6
    assert "那次旅行那天" not in query.alternates


def test_originals_and_summaries_are_separate_lists():
    corpus = [
        _hit("e1", "西湖", source_kind="original"),
        _hit("e2", "杭州概览", source_kind="summary"),
    ]
    lib, _ = _lib(corpus)
    result = lib.retrieve(lib.resolve_query("杭州", "p1"))
    assert {h.evidence_id for h in result.hits} == {"e1"}
    assert {h.evidence_id for h in result.summaries} == {"e2"}


def test_rrf_prefers_hits_voted_by_more_channels():
    corpus = [_hit("e1", "多通道"), _hit("e2", "单通道")]
    search = FakeSearch(corpus)
    search.keyword = lambda q, alternates=(): [corpus[0]]  # only e1 in keyword
    lib = Librarian(search, None)
    result = lib.retrieve(lib.resolve_query("x", "p1"))
    assert result.hits[0].evidence_id == "e1"
    assert result.trace and all(t.channel for t in result.trace)


def test_rrf_k_is_stable():
    assert RRF_K == 60


def test_channel_down_is_partial_not_empty():
    corpus = [_hit("e1", "西湖")]
    lib, _ = _lib(corpus, down=("dense",))
    result = lib.retrieve(lib.resolve_query("x", "p1"))
    assert result.status is ResultStatus.PARTIAL
    assert "dense_unavailable" in result.gaps and result.degraded


def test_all_channels_down_is_empty_without_clarify():
    lib, _ = _lib([_hit("e1", "西湖")], down=("keyword", "dense", "sparse", "time"))
    result = lib.retrieve(lib.resolve_query("她那次", "p1"))
    assert result.status is ResultStatus.EMPTY
    assert result.clarify is None


def test_empty_ambiguous_query_asks_for_time_not_fake_answer():
    lib, _ = _lib([])
    result = lib.retrieve(lib.resolve_query("那次", "p1"))
    assert result.status is ResultStatus.EMPTY and result.clarify


def test_event_window_adds_votes_but_time_never_filters():
    corpus = [_hit("e1", "西湖", channel="keyword")]
    events = FakeEvents([EventHit(event_id="ev1", title="杭州行", day="2025-04-03", summary="", score=1.0)])
    lib, _ = _lib(corpus, events=events)
    query = lib.resolve_query("杭州", "p1", event_from=date(2025, 1, 1), event_to=date(2025, 12, 31))
    result = lib.retrieve(query)
    assert result.event_hit_count == 1
    assert "e1" in {h.evidence_id for h in result.hits}
    assert events.calls == 1


def test_cite_labels_do_not_collide_across_lists():
    corpus = [_hit("e1", "西湖"), _hit("e2", "概览", source_kind="summary")]
    lib, _ = _lib(corpus)
    result = lib.retrieve(lib.resolve_query("x", "p1"))
    labels = {h.cite_as for h in (*result.hits, *result.summaries)}
    assert labels == {"S1", "W1"}


def test_summary_list_is_capped_at_ten():
    corpus = [_hit(f"e{i}", f"概览{i}", source_kind="summary") for i in range(15)]
    lib, _ = _lib(corpus)
    result = lib.retrieve(lib.resolve_query("x", "p1"))
    assert len(result.summaries) <= 10
    assert "results_trimmed" in result.gaps
