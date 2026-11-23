"""Prompt assets: every referenced asset exists, is non-trivial and safe."""

import pytest

from agentv3.prompts import load

NAMES = ("persona", "muse", "sculptor", "provenance", "guard")


@pytest.mark.parametrize("name", NAMES)
def test_asset_loads_and_is_substantial(name):
    text = load(name)
    assert len(text) > 80


def test_guard_asset_never_claims_contact():
    text = load("guard")
    assert "已联络" not in text and "已经通知" not in text


def test_missing_asset_raises():
    with pytest.raises(FileNotFoundError):
        load("does-not-exist")
