"""BB3 真实音频波形端点测试（2026-09-08）

GET /api/v1/contents/{id}/waveform?buckets=N：
- 正常 WAV（PCM16）→ 200 + buckets 数组（0..1 峰值，桶数正确）
- chunk 遍历容错：data 前带 LIST 附注块仍解析成功（不认死 44 偏移）
- 非 WAV 字节 / 非 PCM16 → 404 MEDIA_003 显式错误码（不静默）
- 非本人 → 404 CONTENT_010（IDOR 防护）；不存在 id → 404
- buckets 越界 → 422（FastAPI Query 校验）

WAV 字节全部代码内手写构造，不依赖外部文件。
"""
import struct
import uuid

import pytest
from app.main import app
from fastapi.testclient import TestClient


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def auth_headers(client):
    """登录拿 token（微信 mock）"""
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"bb3-{uuid.uuid4().hex[:8]}", "device_id": "bb3-dev"},
    )
    assert r.status_code == 200
    token = r.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _current_user_id(headers) -> str:
    from app.core.security import decode_token

    token = headers["Authorization"].split(" ", 1)[1]
    return decode_token(token)["sub"]


def _wav_bytes(samples: list[int], extra_chunk: bool = False, bits: int = 16) -> bytes:
    """手写构造 WAV：标准 fmt + （可选 LIST 附注块）+ data，全部代码内字节"""
    fmt_code = 1  # PCM
    if bits == 16:
        data = struct.pack(f"<{len(samples)}h", *samples)
        align = 2
    else:  # 8bit 非 PCM16 反例用
        data = bytes((v + 128) % 256 for v in samples)
        align = 1
    byte_rate = 16000 * 1 * align
    fmt = struct.pack("<HHIIHH", fmt_code, 1, 16000, byte_rate, align, bits)
    out = b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(data)) + b"WAVE"
    out += b"fmt " + struct.pack("<I", len(fmt)) + fmt
    if extra_chunk:
        list_body = b"INFOISFT" + struct.pack("<I", 6) + b"uvue12"
        out += b"LIST" + struct.pack("<I", len(list_body)) + list_body
    out += b"data" + struct.pack("<I", len(data)) + data
    return out


def _seed_voice(client, headers, wav: bytes) -> str:
    """落 storage + 建 voice 内容 → content_id"""
    from app.services.external.storage import get_storage_backend

    user_id = _current_user_id(headers)
    cos_key = f"voice/{user_id}/bb3_{uuid.uuid4().hex[:8]}.wav"
    get_storage_backend().put_object(cos_key, wav)
    r = client.post(
        "/api/v1/contents",
        json={"content_type": "voice", "cos_key": cos_key, "source": "app"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    return r.json()["data"]["id"]


def test_waveform_buckets_and_peaks(client, auth_headers):
    """正常 WAV → 桶数正确 + 已知峰值按 0..1 归一（12800/32768=0.3906、16384/32768=0.5）"""
    samples = [0] * 128
    for i in range(16):
        samples[i] = 12800  # 桶 0
        samples[16 + i] = -16384  # 桶 1
    cid = _seed_voice(client, auth_headers, _wav_bytes(samples))
    r = client.get(
        f"/api/v1/contents/{cid}/waveform", params={"buckets": 8}, headers=auth_headers
    )
    assert r.status_code == 200, r.text
    buckets = r.json()["data"]["buckets"]
    assert len(buckets) == 8  # 显式桶数（128 样本/8 桶=16 对齐）
    assert buckets[0] == pytest.approx(12800 / 32768, abs=1e-3)
    assert buckets[1] == pytest.approx(16384 / 32768, abs=1e-3)
    for v in buckets[2:]:
        assert v == 0.0
    assert all(0.0 <= v <= 1.0 for v in buckets)


def test_waveform_custom_buckets(client, auth_headers):
    """buckets=5 → 返回 5 桶"""
    cid = _seed_voice(client, auth_headers, _wav_bytes([100] * 40))
    r = client.get(
        f"/api/v1/contents/{cid}/waveform", params={"buckets": 5}, headers=auth_headers
    )
    assert r.status_code == 200
    assert len(r.json()["data"]["buckets"]) == 5


def test_waveform_extra_chunk_tolerated(client, auth_headers):
    """data 前带 LIST 附注块 → chunk 遍历容错仍解析成功（不认死 44 偏移）"""
    cid = _seed_voice(client, auth_headers, _wav_bytes([8000] * 32, extra_chunk=True))
    r = client.get(f"/api/v1/contents/{cid}/waveform", headers=auth_headers)
    assert r.status_code == 200, r.text
    buckets = r.json()["data"]["buckets"]
    assert len(buckets) == 28
    assert buckets[0] > 0


def test_waveform_not_wav_explicit_error(client, auth_headers):
    """非 WAV 字节 → 404 MEDIA_003 显式错误码（不静默不伪造）"""
    cid = _seed_voice(client, auth_headers, b"<html>not-a-wav</html>")
    r = client.get(f"/api/v1/contents/{cid}/waveform", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "MEDIA_003"


def test_waveform_not_pcm16_explicit_error(client, auth_headers):
    """8bit WAV（非 PCM16）→ 404 MEDIA_003"""
    cid = _seed_voice(client, auth_headers, _wav_bytes([100] * 32, bits=8))
    r = client.get(f"/api/v1/contents/{cid}/waveform", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "MEDIA_003"


def test_waveform_cross_user_404(client, auth_headers):
    """非本人内容 → 404 CONTENT_010（IDOR 防护，不区分不存在/越权）"""
    samples = [1000] * 32
    other_headers = dict(auth_headers)  # 本测试内自建两用户
    r = client.post(
        "/api/v1/auth/wechat",
        json={"code": f"bb3x-{uuid.uuid4().hex[:8]}", "device_id": "bb3x-dev"},
    )
    assert r.status_code == 200
    other_headers = {
        "Authorization": f"Bearer {r.json()['data']['access_token']}"
    }
    cid = _seed_voice(client, other_headers, _wav_bytes(samples))
    r = client.get(f"/api/v1/contents/{cid}/waveform", headers=auth_headers)
    assert r.status_code == 404
    assert r.json()["code"] == "CONTENT_010"


def test_waveform_missing_content_404(client, auth_headers):
    """不存在的内容 id → 404"""
    r = client.get(
        f"/api/v1/contents/{uuid.uuid4()}/waveform", headers=auth_headers
    )
    assert r.status_code == 404


def test_waveform_buckets_out_of_range_422(client, auth_headers):
    """buckets=0 / 129 → 422（Query 校验）"""
    cid = _seed_voice(client, auth_headers, _wav_bytes([100] * 32))
    for bad in (0, 129):
        r = client.get(
            f"/api/v1/contents/{cid}/waveform",
            params={"buckets": bad},
            headers=auth_headers,
        )
        assert r.status_code == 422
