"""Librarian: returns evidence, never a conclusion.

Design ported from Linger ``memory/README.md`` @6663e21:
  * up to six alternates, each voting as its own RRF list (k=60);
  * the event / date windows vote and reorder, they never filter a source out;
  * two separately ranked lists: summaries (overview) and originals (detail);
  * ``empty`` is only reported after the originals list was actually queried;
  * a failed channel yields ``partial`` (with a gap code), never a quiet empty.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date

from harness.contracts import (
    MAX_ALTERNATE_CHARS,
    MAX_ALTERNATES,
    Hit,
    ResultStatus,
    SearchQuery,
    SearchResult,
    TraceItem,
)
from ports import EventPort, PortError, SearchPort

RRF_K = 60
_AMBIGUOUS = ("那次", "那个", "那件事", "那个人", "她", "他", "那时候的事")


class Librarian:
    def __init__(self, search: SearchPort, events: EventPort | None = None) -> None:
        self._search = search
        self._events = events

    # -- canonical query ----------------------------------------------
    def resolve_query(
        self,
        text: str,
        person_id: str,
        *,
        mode: str = "evidence",
        alternates: Sequence[str] = (),
        date_from: date | None = None,
        date_to: date | None = None,
        event_from: date | None = None,
        event_to: date | None = None,
        as_of: date | None = None,
        limit: int = 20,
        layers: Sequence[str] = (),
    ) -> SearchQuery:
        """The single place that builds a SearchQuery: all channels share it."""
        cleaned: list[str] = []
        for raw in alternates:
            alt = (raw or "").strip()[:MAX_ALTERNATE_CHARS]
            if alt and alt != text.strip() and alt not in cleaned:
                cleaned.append(alt)
        return SearchQuery(
            query=text.strip(),
            person_id=person_id,
            mode=mode,  # type: ignore[arg-type]
            alternates=tuple(cleaned[:MAX_ALTERNATES]),
            date_from=date_from,
            date_to=date_to,
            event_from=event_from,
            event_to=event_to,
            as_of=as_of,
            limit=limit,
            layers=tuple(layers),  # type: ignore[arg-type]
        )

    # -- retrieval ----------------------------------------------------
    def retrieve(self, query: SearchQuery, *, degraded: Sequence[str] = ()) -> SearchResult:
        lists: list[list[Hit]] = []
        gaps: list[str] = []
        channel_down = list(degraded)

        if "search" not in degraded:
            for name, fn in (
                ("keyword", lambda: self._search.keyword(query, alternates=query.alternates)),
                ("dense", lambda: self._search.dense(query)),
                ("sparse", lambda: self._search.sparse(query)),
            ):
                try:
                    lists.append(list(fn()))
                except PortError:
                    channel_down.append(name)
                    gaps.append(f"{name}_unavailable")
            if query.event_from or query.event_to or query.as_of:
                try:
                    lists.append(list(self._search.window(query)))
                except PortError:
                    channel_down.append("time")
                    gaps.append("time_channel_unavailable")
        else:
            channel_down.append("search")
            gaps.append("search_unavailable")

        event_count = 0
        if self._events is not None and (query.event_from or query.event_to):
            try:
                hits = self._events.hits(
                    query.person_id,
                    from_=query.event_from,
                    to=query.event_to,
                    keywords=(query.query, *query.alternates),
                )
                event_count = len(hits)
                lists.append(
                    [
                        Hit(
                            cite_as=f"S{idx + 1}",
                            evidence_id=h.event_id,
                            kind="event",
                            title=h.title,
                            time_hint=h.day,
                            content=h.summary,
                            score=h.score,
                            channel="event",
                        )
                        for idx, h in enumerate(hits)
                    ]
                )
            except PortError:
                channel_down.append("event")
                gaps.append("event_unavailable")

        # RRF over every list; alternates are separate lists inside the keyword channel.
        scores: dict[str, float] = {}
        merged: dict[str, Hit] = {}
        for hits in lists:
            for rank, hit in enumerate(hits):
                scores[hit.evidence_id] = scores.get(hit.evidence_id, 0.0) + 1.0 / (RRF_K + rank + 1)
                if hit.evidence_id not in merged or hit.score > merged[hit.evidence_id].score:
                    merged[hit.evidence_id] = hit

        ordered = sorted(merged.values(), key=lambda h: scores[h.evidence_id], reverse=True)
        ordered = [h.model_copy(update={"score": round(scores[h.evidence_id], 6)}) for h in ordered]

        originals = [h for h in ordered if h.source_kind == "original"]
        summaries = [h for h in ordered if h.source_kind == "summary"]

        trimmed = len(originals) > query.limit or len(summaries) > 10
        if trimmed:
            gaps.append("results_trimmed")
        originals = originals[: query.limit]
        summaries = summaries[:10]

        # Cite labels are assigned per list so [S1] and [W1] never collide.
        originals = [h.model_copy(update={"cite_as": f"S{i + 1}"}) for i, h in enumerate(originals)]
        summaries = [h.model_copy(update={"cite_as": f"W{i + 1}"}) for i, h in enumerate(summaries)]

        status = ResultStatus.GROUNDED
        if not originals and not summaries:
            status = ResultStatus.EMPTY
        if channel_down or trimmed:
            status = ResultStatus.PARTIAL if (originals or summaries) else ResultStatus.EMPTY

        clarify = None
        if status is ResultStatus.EMPTY and query.mode == "evidence" and not channel_down:
            if any(word in query.query for word in _AMBIGUOUS):
                clarify = "你指的是哪一次？给我一个大概的时间或地点，我就能找得更准。"

        trace = tuple(
            TraceItem(claim=h.title, evidence_id=h.evidence_id, channel=h.channel) for h in (*summaries, *originals)
        )

        return SearchResult(
            hits=tuple(originals),
            summaries=tuple(summaries),
            reason="rrf" if ordered else "no_match",
            relaxed=bool(query.alternates and ordered),
            clarify=clarify,
            event_hit_count=event_count,
            trace=trace,
            degraded=bool(channel_down),
            gaps=tuple(sorted(set(gaps))),
            status=status,
        )
