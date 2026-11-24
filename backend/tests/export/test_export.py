"""B5-04 export probes: truncated must carry a continuation cursor."""

from __future__ import annotations

import pytest
from app.services import export as module


class _Scalars:
    def __init__(self, rows): self._rows = rows
    def all(self): return self._rows


class _Result:
    def __init__(self, rows): self._rows = rows
    def scalars(self): return _Scalars(self._rows)


class _DB:
    def execute(self, _stmt): return _Result([])


def test_cursor_roundtrip_and_invalid_input():
    # B5-04：游标值按真实主键格式构造（contents 是 UUID 列，memory.entry_id 是 m_<12hex>）
    values = {
        "contents": "00000000-0000-0000-0000-000000000001",
        "memory": "m_000000000001",
    }
    assert module.decode_cursor(module.encode_cursor(values)) == values
    with pytest.raises(ValueError):
        module.decode_cursor("not-base64")


def test_truncated_payload_has_next_cursor(monkeypatch):
    class Row:
        def __init__(self, id): self.id = id

    cid = "00000000-0000-0000-0000-000000000001"

    def fake_collect(_db, model, _user_id, cap=None, after=None, **_kw):
        if model is module.Content:
            return [Row(cid)], True, cid
        return [], False, None

    monkeypatch.setattr(module, "_collect_capped", fake_collect)
    monkeypatch.setattr(module, "_profile_out", lambda db, user_id: {"dimensions": {}})
    monkeypatch.setattr(module, "_content_out", lambda c, *_: {"content_id": c.id})
    payload = module.build_export_payload(_DB(), "u-1")
    assert payload["truncated"] is True
    assert payload["next_cursor"]
    assert module.decode_cursor(payload["next_cursor"])["contents"] == cid


def test_untruncated_payload_has_no_cursor(monkeypatch):
    monkeypatch.setattr(module, "_collect_capped", lambda *_a, **_k: ([], False, None))
    monkeypatch.setattr(module, "_profile_out", lambda db, user_id: {"dimensions": {}})
    payload = module.build_export_payload(_DB(), "u-1")
    assert payload["truncated"] is False
    assert payload["next_cursor"] is None
    assert payload["memory"] == [] and payload["persons"] == []


def test_zip_contains_text_voice_and_metadata_but_not_photo_bytes():
    import io
    import json
    import zipfile

    class Storage:
        def get_object(self, key):
            assert key == "voice/v1.wav"
            return b"RIFF"

    payload = {
        "truncated": False,
        "contents": [
            {"content_id": "t1", "content_type": "text", "caption": "今天的风"},
            {"content_id": "v1", "content_type": "voice", "caption": "转写文本", "url": "voice/v1.wav"},
            {"content_id": "p1", "content_type": "photo", "url": "photo/p1.jpg", "download_url": "https://cos/p1"},
        ],
    }
    archive, truncated = module.build_export_zip(payload, storage=Storage())
    assert truncated is False
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = set(zf.namelist())
        assert "metadata.json" in names
        assert "texts/t1.txt" in names
        assert "transcripts/v1.txt" in names
        assert "audio/v1.wav" in names
        assert not any(name.startswith("photos/") for name in names)
        meta = json.loads(zf.read("metadata.json"))
        assert meta["contents"][2]["download_url"] == "https://cos/p1"


def test_photo_export_gets_signed_cos_links_not_binary():
    class Content:
        id = "p1"
        content_type = "photo"
        taken_at = None
        text = ""
        cos_key = "photo/original.jpg"
        thumbnail_key = "photo/thumb.jpg"
        gps_lat = None
        gps_lng = None
        place = None
        emotion = None
        sensitive_status = None
        source = None
        status = None
        created_at = None
        deleted_at = None

    class Storage:
        def get_download_url(self, key, user_id="", ttl=900):
            return f"https://cos.example/{key}?sign=1"

    out = module._content_out(Content(), Storage(), "u1")
    assert out["download_url"] == "https://cos.example/photo/original.jpg?sign=1"
    assert out["thumbnail_url"] == "https://cos.example/photo/thumb.jpg?sign=1"
    assert "binary" not in out
