"""照片定位：坐标合并口径 + WGS84→GCJ-02 对拍（2026-10-16 · 根因族 RC-B「静默降级」收官）。

## 钉住什么（可证伪）

1. **坐标系**：客户端 `type:'wgs84'` 与照片 EXIF GPS 都是 WGS84，而高德 REST regeo
   只认 GCJ-02（无 coordsys 参数可切）⇒ 少一次转换会让地名**系统性偏移数百米**
   ——"有地点但错位"比"没地点"更像 bug。用北京 / 上海各一个**公开已知坐标对**对拍，
   误差 < 50m（实测：北京 10.2m / 上海 0.0m）。
2. **合并口径**：**EXIF 优先（拍摄地真值），meta 兜底（设备当前定位）** —— 依据见
   `app/api/contents/__init__.py` 合并处注释。
3. **无定位 / 空哨兵**：既不得编造地名，也不得把 (0,0) 当坐标入库（高德会对几内亚湾
   返回一个**看似正常**的地名）。`is_usable_gps` + `parse_photo_meta` 归一为 None
   ⇒ 留空（诚实缺省），绝不写假值。
"""
from __future__ import annotations

import io
import json
import math
import uuid

import pytest
from app.db.models import Content
from app.db.session import SessionLocal
from app.main import app
from app.services import upload as upload_svc
from app.services.external.gcj02 import wgs84_to_gcj02
from app.services.external.storage import get_storage_backend
from app.services.upload_meta import is_usable_gps, parse_photo_meta, resolve_photo_gps
from fastapi.testclient import TestClient

# =====================================================================
# 通用工具
# =====================================================================

def _distance_m(a: tuple[float, float], b: tuple[float, float]) -> float:
    """两点球面距离（米），haversine。"""
    radius = 6_371_000.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp = p2 - p1
    dl = math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(h))


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"gps-{uuid.uuid4().hex[:8]}", "device_id": "gps-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _jpeg_bytes() -> bytes:
    """无 EXIF 的有效 JPEG（魔数校验需要真实照片字节）。"""
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (10, 120, 200)).save(buf, format="JPEG")
    return buf.getvalue()


def _jpeg_with_gps() -> bytes:
    """构造带 EXIF GPS 的最小 JPEG（上海外滩 31.2303N / 121.4736E）。

    ⚠️ DMS 是 RATIONAL 数组，写入必须用 `IFDRational`（直接塞 int 元组会在 Pillow
    序列化时炸）—— 构造侧 API 要求，与解析侧无关。
    """
    from PIL import Image
    from PIL.TiffImagePlugin import IFDRational

    exif = Image.Exif()
    gps = exif.get_ifd(0x8825)  # GPSInfo IFD
    gps[1] = "N"
    gps[2] = (IFDRational(31, 1), IFDRational(13, 1), IFDRational(49, 1))
    gps[3] = "E"
    gps[4] = (IFDRational(121, 1), IFDRational(28, 1), IFDRational(25, 1))
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), (128, 128, 128)).save(buf, format="JPEG", exif=exif.tobytes())
    return buf.getvalue()


def _upload(client, headers, *, content: bytes, meta: dict):
    files = {"file": ("photo.jpg", content, "image/jpeg")}
    data = {"meta": json.dumps(meta, ensure_ascii=False)}
    return client.post("/api/v1/contents/upload", files=files, data=data, headers=headers)


def _stored_gps(content_id: str) -> tuple[float | None, float | None]:
    """直查 DB 拿落库坐标（ContentOut 不回显 gps_lat/gps_lng，只能查表）。"""
    db = SessionLocal()
    try:
        row = db.get(Content, uuid.UUID(content_id))
        assert row is not None
        return row.gps_lat, row.gps_lng
    finally:
        db.close()


# =====================================================================
# 1. 坐标系：WGS84 → GCJ-02 对拍（公开已知坐标对，误差 < 50m）
# =====================================================================

# 参考对（公开广泛复现）：北京天安门一带 / 上海（eviltransform 经典样例）。
_BEIJING_WGS84 = (39.908722, 116.397499)
_BEIJING_GCJ02 = (39.910035, 116.403725)
_SHANGHAI_WGS84 = (31.1774276, 121.5272106)
_SHANGHAI_GCJ02 = (31.17530398364597, 121.531541859215)


def test_geo_conversion_beijing_within_50m():
    got = wgs84_to_gcj02(*_BEIJING_WGS84)
    assert _distance_m(got, _BEIJING_GCJ02) < 50.0, f"北京对拍超差: {got}"


def test_geo_conversion_shanghai_within_50m():
    got = wgs84_to_gcj02(*_SHANGHAI_WGS84)
    assert _distance_m(got, _SHANGHAI_GCJ02) < 50.0, f"上海对拍超差: {got}"


def test_geo_conversion_offset_is_domestic_scale():
    """必须真的转了（数百米量级）——否则"没转"也会通过上面的对拍容差。"""
    src = (31.2304, 121.4737)  # 上海外滩
    dst = wgs84_to_gcj02(*src)
    assert 300 < _distance_m(src, dst) < 800


# =====================================================================
# 2. 坐标可用性判定（空哨兵 / NaN / 越界）
# =====================================================================

@pytest.mark.parametrize(
    "lat,lng",
    [(0.0, 0.0), (None, 121.0), (31.0, None), (float("nan"), 121.0), (999.0, 121.0), (31.0, 200.0)],
)
def test_gps_unusable_cases(lat, lng):
    """(0,0) 空哨兵 / 缺一边 / NaN / 越界 ⇒ 不可用。"""
    assert is_usable_gps(lat, lng) is False


@pytest.mark.parametrize("lat,lng", [(31.2304, 121.4737), (0.0, 121.4737), (31.2304, 0.0)])
def test_gps_usable_cases(lat, lng):
    """正常坐标可用；**单边为 0 合法**（本初子午线 / 赤道），不拦。"""
    assert is_usable_gps(lat, lng) is True


def test_parse_meta_zero_sentinel_becomes_none():
    """meta 里 (0,0) 是"设备没定位到"的空哨兵 ⇒ 归一为 None，不当作真实坐标。"""
    m = parse_photo_meta(json.dumps({"gps_lat": 0, "gps_lng": 0, "source": "app"}))
    assert m.gps_lat is None and m.gps_lng is None


# =====================================================================
# 3. 上传合并：EXIF 优先 / meta 兜底 / 空哨兵留空
# =====================================================================

def test_upload_geo_exif_wins_over_meta(client, auth_headers):
    """EXIF（拍摄地真值）优先于 meta（设备当前定位）—— 相册选旧照片场景的关键。"""
    try:
        content = _jpeg_with_gps()
    except Exception as exc:  # noqa: BLE001 —— 构造侧 Pillow 能力问题
        pytest.skip(f"本机 Pillow 无法写出 GPS IFD（构造侧限制）：{exc}")
    # meta 故意给一个完全不同的有效坐标（北京）
    meta = {
        "source": "app",
        "gps_lat": _BEIJING_WGS84[0],
        "gps_lng": _BEIJING_WGS84[1],
        "perceptual_hash": f"ph-{uuid.uuid4().hex[:10]}",
    }
    r = _upload(client, auth_headers, content=content, meta=meta)
    assert r.status_code == 200, r.text
    lat, lng = _stored_gps(r.json()["data"]["id"])
    assert lat is not None and lng is not None
    assert abs(lat - 31.230) < 0.01 and abs(lng - 121.474) < 0.01, f"应采用 EXIF 坐标，实得 {lat},{lng}"


def test_upload_geo_meta_fallback_when_no_exif(client, auth_headers):
    """照片无 EXIF GPS ⇒ 用 meta 坐标兜底（否则截图 / 剥离 EXIF 的照片永远没地点）。"""
    meta = {
        "source": "app",
        "gps_lat": _BEIJING_WGS84[0],
        "gps_lng": _BEIJING_WGS84[1],
        "perceptual_hash": f"ph-{uuid.uuid4().hex[:10]}",
    }
    r = _upload(client, auth_headers, content=_jpeg_bytes(), meta=meta)
    assert r.status_code == 200, r.text
    lat, lng = _stored_gps(r.json()["data"]["id"])
    assert lat == pytest.approx(_BEIJING_WGS84[0]) and lng == pytest.approx(_BEIJING_WGS84[1])


def test_upload_geo_zero_sentinel_left_empty(client, auth_headers):
    """meta 给 (0,0) 空哨兵 ⇒ 落库为空（不写 0/0、不逆地理出几内亚湾地名）。"""
    meta = {"source": "app", "gps_lat": 0, "gps_lng": 0, "perceptual_hash": f"ph-{uuid.uuid4().hex[:10]}"}
    r = _upload(client, auth_headers, content=_jpeg_bytes(), meta=meta)
    assert r.status_code == 200, r.text
    lat, lng = _stored_gps(r.json()["data"]["id"])
    assert lat is None and lng is None


# =====================================================================
# 4. 合并函数（单一实现）+ 分片 complete 链路：同一口径
# =====================================================================

def _exif_jpeg_or_skip() -> bytes:
    try:
        return _jpeg_with_gps()
    except Exception as exc:  # noqa: BLE001 —— 构造侧 Pillow 能力问题
        pytest.skip(f"本机 Pillow 无法写出 GPS IFD（构造侧限制）：{exc}")


def test_resolve_photo_gps_single_source():
    """合并函数本身：EXIF 优先 / meta 兜底 / 都不可用 ⇒ (None, None)。"""
    exif_jpeg = _exif_jpeg_or_skip()
    # EXIF（上海外滩）压过 meta（北京）
    lat, lng = resolve_photo_gps(_BEIJING_WGS84[0], _BEIJING_WGS84[1], exif_jpeg)
    assert abs(lat - 31.230) < 0.01 and abs(lng - 121.474) < 0.01
    # 无 EXIF（无 GPS 的 JPEG / 非图片字节）⇒ meta 兜底
    for data in (_jpeg_bytes(), b"not an image", None):
        lat2, lng2 = resolve_photo_gps(_BEIJING_WGS84[0], _BEIJING_WGS84[1], data)
        assert lat2 == pytest.approx(_BEIJING_WGS84[0]) and lng2 == pytest.approx(_BEIJING_WGS84[1])
    # 两侧都不可用 ⇒ 留空（不编造）
    assert resolve_photo_gps(0.0, 0.0, b"not an image") == (None, None)
    assert resolve_photo_gps(None, None, b"not an image") == (None, None)


def _register_chunked(db, user, content: bytes, meta_json: str) -> Content:
    """模拟分片 complete：对象已在存储 → register_photo_content 建记录 → 回读。"""
    key = f"photos/{user.id}/202610/geo_{uuid.uuid4().hex[:8]}.jpg"
    get_storage_backend().put_object(key, content)
    cid = upload_svc.register_photo_content(db, user.id, key, meta_json)
    row = db.get(Content, uuid.UUID(cid))
    assert row is not None
    return row


def test_chunked_register_geo_exif_wins_when_meta_missing(db_user):
    """分片 complete 链路：客户端 meta **无坐标**、照片有 EXIF GPS ⇒ 落库有坐标。

    修前该链路从不看 EXIF ⇒ 这张照片恒定丢地点（本批要治的静默丢失）；有坐标后
    `enrich_place_on_ingest` 才能拿到坐标去逆地理（→ `place` 非空）。
    """
    db, user = db_user
    row = _register_chunked(db, user, _exif_jpeg_or_skip(), '{"source":"app"}')
    assert row.gps_lat is not None and row.gps_lng is not None, "EXIF GPS 未被采纳"
    assert abs(row.gps_lat - 31.230) < 0.01 and abs(row.gps_lng - 121.474) < 0.01


def test_chunked_register_geo_exif_beats_meta(db_user):
    """分片链路同为 EXIF 优先：meta 给北京、EXIF 是上海外滩 ⇒ 落库为 EXIF。"""
    db, user = db_user
    meta = json.dumps({"source": "app", "gps_lat": _BEIJING_WGS84[0], "gps_lng": _BEIJING_WGS84[1]})
    row = _register_chunked(db, user, _exif_jpeg_or_skip(), meta)
    assert row.gps_lat is not None and abs(row.gps_lat - 31.230) < 0.01


def test_chunked_register_geo_meta_fallback(db_user):
    """分片链路：照片无 EXIF GPS ⇒ meta 兜底（否则截图 / 剥离 EXIF 的照片永远没地点）。"""
    db, user = db_user
    meta = json.dumps({"source": "app", "gps_lat": _BEIJING_WGS84[0], "gps_lng": _BEIJING_WGS84[1]})
    row = _register_chunked(db, user, _jpeg_bytes(), meta)
    assert row.gps_lat == pytest.approx(_BEIJING_WGS84[0])
    assert row.gps_lng == pytest.approx(_BEIJING_WGS84[1])
