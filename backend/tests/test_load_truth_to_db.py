"""真值 JSON → 本地库 的纯函数单测（scripts/load_truth_to_db.py）

只覆盖不需要 PG/Qdrant 的逻辑：稳定 id 派生、正文提取、批次读取。
集成部分（真落库 + 检索自检）需本地 PG，属 `-m integration`，由人工按
`scripts/load_truth_to_db.py --batches b,c --self-check` 跑，不在此文件内。
"""
from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent.parent
for _p in (_ROOT, _ROOT / "backend", _ROOT / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import scripts.build_truth_corpus as btc  # noqa: E402
import scripts.load_truth_to_db as loader  # noqa: E402

pytestmark = pytest.mark.unit


def test_stable_content_id_matches_build_truth_corpus():
    """两个脚本必须派生出**同一个** content_id，否则 A 批 remap 与灌库对不上。"""
    for batch, cid in (("b", "a-0001"), ("c", "a-0001"), ("d", "p-0001")):
        assert loader.stable_content_id(batch, cid) == btc._stable_uuid(batch, cid)


def test_stable_content_id_is_deterministic_and_scoped():
    once = loader.stable_content_id("b", "a-0001")
    assert once == loader.stable_content_id("b", "a-0001")
    # 同一采集 id 在不同批次必须不同（B/C/D 三批存在同名 id）
    assert once != loader.stable_content_id("c", "a-0001")
    assert once != loader.stable_content_id("d", "a-0001")
    uuid.UUID(once)  # 必须是合法 uuid（contents.id 是 UUID 列）


def test_stable_user_id_is_deterministic_and_valid_uuid():
    hashed = "sha256:" + "a" * 64
    once = loader.stable_user_id(hashed)
    assert once == loader.stable_user_id(hashed)
    assert once != loader.stable_user_id("sha256:" + "b" * 64)
    uuid.UUID(once)


def test_item_text_per_batch():
    assert loader.item_text("b", {"text": "明天记得带身份证"}) == "明天记得带身份证"
    assert loader.item_text("c", {"transcript": "今天去了咖啡馆"}) == "今天去了咖啡馆"
    # D 批无自然语言正文 → 用主题拼装
    d_text = loader.item_text("d", {"expected_l1": [{"theme": "早上通勤"}]})
    assert d_text.startswith("照片事件：") and "早上通勤" in d_text
    assert loader.item_text("d", {"set_id": "p-0001"}) == "照片事件：p-0001"


def test_load_batch_records_picks_highest_version(tmp_path):
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "b_v1.json").write_text(
        json.dumps([{"fragment_id": "f-0001"}], ensure_ascii=False), encoding="utf-8")
    (tmp_path / "b" / "b_v2.json").write_text(
        json.dumps([{"fragment_id": "f-0002"}], ensure_ascii=False), encoding="utf-8")

    records, path = loader.load_batch_records("b", tmp_path)

    assert path.name == "b_v2.json"
    assert records[0]["fragment_id"] == "f-0002"


def test_load_batch_records_missing_returns_empty(tmp_path):
    records, path = loader.load_batch_records("d", tmp_path)
    assert records == [] and path is None


def test_parse_iso_handles_bad_input():
    assert loader._parse_iso(None) is None
    assert loader._parse_iso("not-a-date") is None
    parsed = loader._parse_iso("2026-09-01T10:00:00+08:00")
    assert parsed is not None and parsed.year == 2026
