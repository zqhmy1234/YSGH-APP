"""Event domain: four deterministic layers over ingested episodes.

moment → activity → episode → chapter. The rules are intentionally boring:
same day+place for an activity, consecutive days in one place for an episode,
same month+place for a chapter. A model may re-title nodes later, but never
re-groups them.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import timedelta
from typing import Sequence

from ..contracts import Episode, EventNode


class EventAggregator:
    def aggregate(self, episodes: Sequence[Episode]) -> list[EventNode]:
        nodes: list[EventNode] = []
        for ep in episodes:
            nodes.append(
                EventNode(node_id=f"m_{ep.episode_id}", layer="moment", title=_title(ep), day=ep.day, member_ids=(ep.episode_id,))
            )

        by_day_place: dict[tuple, list[Episode]] = defaultdict(list)
        for ep in episodes:
            by_day_place[(ep.day, ep.place)].append(ep)
        for (day, place), group in sorted(by_day_place.items(), key=lambda kv: (kv[0][0], kv[0][1])):
            if len(group) >= 2:
                nodes.append(
                    EventNode(
                        node_id=f"a_{day.isoformat()}_{abs(hash(place)) % 10000}",
                        layer="activity",
                        title=f"{place or '未标注地点'}的一天",
                        day=day,
                        member_ids=tuple(e.episode_id for e in group),
                    )
                )

        by_place: dict[str, list[Episode]] = defaultdict(list)
        for ep in episodes:
            by_place[ep.place].append(ep)
        for place, group in by_place.items():
            ordered = sorted({e.day for e in group})
            run: list = []
            for day in ordered:
                if run and (day - run[-1]) > timedelta(days=1):
                    if len(run) >= 2:
                        nodes.append(_episode_node(place, run, group))
                    run = []
                run.append(day)
            if len(run) >= 2:
                nodes.append(_episode_node(place, run, group))

        by_month_place: dict[tuple[str, str], list[Episode]] = defaultdict(list)
        for ep in episodes:
            by_month_place[(ep.day.strftime("%Y-%m"), ep.place)].append(ep)
        for (month, place), group in by_month_place.items():
            if len(group) >= 3:
                nodes.append(
                    EventNode(
                        node_id=f"c_{month}_{abs(hash(place)) % 10000}",
                        layer="chapter",
                        title=f"{month} · {place or '多地'}",
                        day=min(e.day for e in group),
                        member_ids=tuple(e.episode_id for e in group),
                    )
                )
        return nodes


def _episode_node(place: str, days: list, group: Sequence[Episode]) -> EventNode:
    members = tuple(e.episode_id for e in group if e.day in set(days))
    return EventNode(
        node_id=f"ep_{days[0].isoformat()}_{days[-1].isoformat()}_{abs(hash(place)) % 10000}",
        layer="episode",
        title=f"{days[0].month}月{place or '一段日子'}",
        day=days[0],
        member_ids=members,
    )


def _title(ep: Episode) -> str:
    return ep.text[:24]
