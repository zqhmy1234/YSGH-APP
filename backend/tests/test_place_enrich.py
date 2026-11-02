"""地名补全钩子回归测试（2026-10-04 · 替换写死的「上海 · 江边」）。

锁住三件事：
1. 带 GPS 的内容产出地名（此前只有照片有，文字/语音即使带坐标也place 恒空）
2. 无 GPS 时**不编造**地名（place 留空）—— 本轮核心口径
3. 顺序不变量：地名补全必须先于会 commit 的钩子，否则读 geo_cache 撞
   closed transaction、place 恒空（实测踩出来的，只报 WARNING 极易被误判成
   「高德不稳定」）
"""
import inspect
import uuid

from app.db.models import Content
from app.db.session import SessionLocal
from app.services.external import amap
from app.services.pipeline_ext import enrich_place_on_ingest
from sqlalchemy import select

USER_ID = uuid.UUID("e4134743-53bd-4ee8-9cd8-05d0917ae2a9")
BUND_LAT, BUND_LNG = 31.2400, 121.4900


def _make(**kw):
    kw.setdefault("content_type", "text")
    kw.setdefault("text", "在江边散步，看到对岸的灯光亮起来。")
    db = SessionLocal()
    try:
        c = Content(user_id=USER_ID, source="app", status="processing", **kw)
        db.add(c)
        db.commit()
        return str(c.id)
    finally:
        db.close()


def _read(cid):
    db = SessionLocal()
    try:
        return db.execute(select(Content).where(Content.id == uuid.UUID(cid))).scalar_one()
    finally:
        db.close()


def test_gps_produces_place(monkeypatch):
    monkeypatch.setattr(amap, "get_place", lambda db, lat, lng: "上海市黄浦区外滩街道")
    cid = _make(gps_lat=BUND_LAT, gps_lng=BUND_LNG)
    db = SessionLocal()
    try:
        c = db.get(Content, uuid.UUID(cid))
        enrich_place_on_ingest(db, c)
        db.commit()
    finally:
        db.close()
    assert _read(cid).place == "上海市黄浦区外滩街道"


def test_no_gps_no_fake_place(monkeypatch):
    """无坐标 → 不调高德、不写 place。绝不用任何占位地名顶替。"""
    calls = []
    monkeypatch.setattr(amap, "get_place", lambda db, lat, lng: calls.append(1) or "不该被调用")
    cid = _make()
    db = SessionLocal()
    try:
        c = db.get(Content, uuid.UUID(cid))
        enrich_place_on_ingest(db, c)
        db.commit()
    finally:
        db.close()
    assert calls == []
    assert _read(cid).place in (None, "")


def test_idempotent(monkeypatch):
    """已有 place → 跳过，不重复调高德。"""
    calls = []
    monkeypatch.setattr(amap, "get_place", lambda db, lat, lng: calls.append(1) or "新值")
    cid = _make(gps_lat=BUND_LAT, gps_lng=BUND_LNG)
    db = SessionLocal()
    try:
        c = db.get(Content, uuid.UUID(cid))
        c.place = "已有地名"
        enrich_place_on_ingest(db, c)
        db.commit()
    finally:
        db.close()
    assert calls == []
    assert _read(cid).place == "已有地名"


def test_amap_failure_is_swallowed_and_leaves_no_fake(monkeypatch):
    """高德不可用 → **不抛、不写假地名**（钩子失败不阻塞入库是设计口径）。"""

    def boom(db, lat, lng):
        raise RuntimeError("高德不可用")

    monkeypatch.setattr(amap, "get_place", boom)
    cid = _make(gps_lat=BUND_LAT, gps_lng=BUND_LNG)
    db = SessionLocal()
    try:
        c = db.get(Content, uuid.UUID(cid))
        enrich_place_on_ingest(db, c)  # 不抛即通过
        db.commit()
    finally:
        db.close()
    assert _read(cid).place in (None, "")


def test_voice_also_gets_place(monkeypatch):
    """语音与文字同口径（此前语音也没地点）。"""
    monkeypatch.setattr(amap, "get_place", lambda db, lat, lng: "上海市黄浦区")
    cid = _make(content_type="voice", text="在江边", gps_lat=BUND_LAT, gps_lng=BUND_LNG)
    db = SessionLocal()
    try:
        c = db.get(Content, uuid.UUID(cid))
        enrich_place_on_ingest(db, c)
        db.commit()
    finally:
        db.close()
    assert _read(cid).place == "上海市黄浦区"


def test_hooks_ordering_invariant():
    """源码级不变量：地名补全必须排在会 commit 的钩子**之前**。

    不锁这个，place 会「看起来随机地空」，且只在 WARNING 留一句 closed
    transaction，排查成本极高（本次即踩过）。
    """
    from app.services import pipeline, pipeline_photo

    def pos(fn, needle):
        src = inspect.getsource(fn)
        i = src.find(needle)
        assert i >= 0, f"{fn.__name__} 缺少 {needle} 调用"
        return i

    early = "enrich_place_on_ingest(db, content)"
    assert pos(pipeline._process_text, early) < pos(
        pipeline._process_text, "mark_sensitive_on_ingest(db, content)"
    )
    assert pos(pipeline._process_text, early) < pos(
        pipeline._process_text, "annotate_on_ingest(db, content)"
    )
    assert pos(pipeline._process_voice, early) < pos(
        pipeline._process_voice, "annotate_on_ingest(db, content)"
    )
    assert pos(pipeline_photo._process_photo, early) < pos(
        pipeline_photo._process_photo, "mark_sensitive_on_ingest(db, content)"
    )
