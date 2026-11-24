"""Crisis screening: no contact claims, no resource numbers in the agent package."""

from __future__ import annotations

from pathlib import Path

from harness.crisis import scan_local


def test_t3_marker_detected():
    assert scan_local("我真的不想活了").level == "t3"


def test_normal_text_is_none():
    assert scan_local("今天有点累").level == "none"


def test_agent_package_contains_no_hotline_number():
    src = Path(__file__).resolve().parents[2] / "src"
    for path in src.rglob("*.py"):
        assert "12356" not in path.read_text(encoding="utf-8"), path
