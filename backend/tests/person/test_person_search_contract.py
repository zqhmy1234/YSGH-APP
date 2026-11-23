"""B5-03 probes: person_id is a first-class search filter, not a dropped key."""

from __future__ import annotations

from app.schemas.search import SearchQuery
from app.services.search_filters import FILTER_KEYS, normalize_filters


def test_person_id_is_in_the_single_filter_key_set():
    assert "person_id" in FILTER_KEYS
    assert normalize_filters({"person_id": "p-1"}) == {"person_id": "p-1"}


def test_search_query_accepts_person_id():
    query = SearchQuery(q="找和小林的照片", person_id="p-1")
    assert query.person_id == "p-1"
    assert SearchQuery(q="全部照片").person_id is None
