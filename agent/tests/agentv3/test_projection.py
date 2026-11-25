"""Digest projection: eight sections, 600 chars, three honest states."""

from datetime import date, datetime

from harness.contracts import MemoryEntry, MemoryLayer
from memory import projection


def _entry(i: int, text: str, layer: MemoryLayer = MemoryLayer.FACT) -> MemoryEntry:
    return MemoryEntry(
        entry_id=f"m{i}",
        person_id="p1",
        layer=layer,
        text=text,
        valid_from=date(2026, 11, 1),
        recorded_at=datetime(2026, 11, 1, 9, 0),
        evidence=(f"e{i}",),
    )


def test_missing_input_is_unavailable_not_empty():
    assert projection.build(None, "p1").state == "unavailable"


def test_too_few_entries_is_insufficient():
    assert projection.build([_entry(1, "她喜欢桂花")], "p1").state == "insufficient_data"


def test_sections_are_named_and_within_budget():
    entries = [_entry(i, f"事实{i}", layer=MemoryLayer.FACT) for i in range(10)]
    entries += [_entry(20 + i, f"偏好{i}", layer=MemoryLayer.PREFERENCE) for i in range(4)]
    digest = projection.build(entries, "p1")
    assert digest.state == "ok"
    assert digest.chars <= 600
    names = [s.name for s in digest.sections]
    assert names and set(names) <= set(projection.SECTIONS)


def test_preference_goes_to_preference_section():
    entries = [_entry(i, "事实", layer=MemoryLayer.FACT) for i in range(4)]
    entries.append(_entry(9, "不吃香菜", layer=MemoryLayer.PREFERENCE))
    digest = projection.build(entries, "p1")
    names = {s.name for s in digest.sections}
    assert "偏好" in names
