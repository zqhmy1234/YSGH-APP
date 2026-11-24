"""B5-02 probes: three states never collapse into an empty list."""

from __future__ import annotations

from app.domain.memory.read_model import RawMemory, build_snapshot, unavailable_snapshot
from app.ports.memory import Correction, NarrativeEpisode


def test_empty_account_is_insufficient_data_with_note():
    snapshot = build_snapshot("u1", RawMemory(note="empty_account"))
    assert snapshot.status == "insufficient_data"
    assert snapshot.note == "empty_account"
    assert snapshot.narrative == ()


def test_any_real_layer_is_ok():
    snapshot = build_snapshot(
        "u1",
        RawMemory(
            narrative=(NarrativeEpisode(entry_id="m1", text="西湖边", evidence=("c1",)),),
            corrections=(Correction("entry", "m1", "text", "新的"),),
        ),
    )
    assert snapshot.status == "ok"


def test_ok_digest_alone_is_ok():
    snapshot = build_snapshot("u1", RawMemory(digest={"status": "ok", "text": "身份：教师"}))
    assert snapshot.status == "ok"


def test_unavailable_is_explicit_and_not_empty_ok():
    snapshot = unavailable_snapshot("u1", note="store_error:OperationalError")
    assert snapshot.status == "unavailable"
    assert snapshot.narrative == () and snapshot.note.startswith("store_error:")
