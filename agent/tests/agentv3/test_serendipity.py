"""Serendipity: one-year-ago recall, cache-only, never writes."""

from datetime import date

from agentv3.agents.serendipity import Serendipity
from agentv3.contracts import ResultStatus, SearchResult


def _retrieve(result: SearchResult):
    calls: list[str] = []

    def fn(query):
        calls.append(query.query)
        return result

    return fn, calls


def test_year_ago_handles_leap_day():
    assert Serendipity.year_ago(date(2028, 2, 29)) == date(2027, 2, 28)


def test_warm_caches_non_empty_result():
    result = SearchResult(status=ResultStatus.GROUNDED)
    fn, calls = _retrieve(result)
    serendipity = Serendipity(fn)
    out = serendipity.warm("p1", topics=["杭州"], today=date(2026, 11, 23))
    assert out["杭州"] is result and calls == ["杭州"]
    assert serendipity.take("p1", "杭州") is result


def test_empty_result_is_not_cached():
    fn, _ = _retrieve(SearchResult(status=ResultStatus.EMPTY))
    serendipity = Serendipity(fn)
    serendipity.warm("p1", topics=["杭州"], today=date(2026, 11, 23))
    assert serendipity.take("p1", "杭州") is None
    assert serendipity.hit_count() == 0
