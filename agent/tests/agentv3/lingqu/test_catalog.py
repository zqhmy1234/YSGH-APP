from __future__ import annotations

from pathlib import Path

import pytest

from adapters.lingqu.catalog import Catalog, CatalogError, load_catalog
from adapters.lingqu.contracts import ProviderId

CATALOG = Path(__file__).resolve().parents[3] / "config" / "lingqu-catalog.json"


def test_catalog_has_five_providers_and_deepseek_default():
    catalog = load_catalog(CATALOG)
    assert {p.provider for p in catalog.profiles()} == set(ProviderId)
    assert len(catalog.profiles()) == 13
    assert catalog.default_profile == "deepseek/deepseek-v4.1-flash"
    assert catalog.default().provider is ProviderId.DEEPSEEK


def test_every_profile_has_source_and_verified_at():
    catalog = load_catalog(CATALOG)
    for profile in catalog.profiles():
        assert profile.source, profile.id
        assert profile.verified_at, profile.id
        assert profile.context_window >= profile.max_output


def test_duplicate_id_rejected():
    catalog = load_catalog(CATALOG)
    first = catalog.profiles()[0]
    with pytest.raises(CatalogError, match="duplicate"):
        Catalog([first, first], default_profile=first.id, catalog_version="x", verified_at="x")


def test_default_must_be_deepseek(tmp_path):
    payload = CATALOG.read_text(encoding="utf-8").replace(
        '"default_profile": "deepseek/deepseek-v4.1-flash"',
        '"default_profile": "qwen/qwen4-max"',
    )
    path = tmp_path / "catalog.json"
    path.write_text(payload, encoding="utf-8")
    with pytest.raises(CatalogError, match="DeepSeek"):
        load_catalog(path)


def test_unknown_profile(monkeypatch):
    catalog = load_catalog(CATALOG)
    with pytest.raises(CatalogError, match="unknown profile"):
        catalog.get("nope/nope")
