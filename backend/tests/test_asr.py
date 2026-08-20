"""ASR 双通道 + 护栏 API 测试（S2-04 · 接口先行，mock 模式零费用）

覆盖：
  - 服务层：mock 兜底确定性输出（未配 key）；通道降级链路记录；文件缺失报错
  - 通道逻辑：preferred 通道真实模式下失败 → 降级（monkeypatch 不联网）
  - API 层：transcribe 上传校验（空/非 wav/超限）；认证保护；护栏集成
  - 护栏：guard/check 放行 + fail-safe 拦截语义
"""
import io
import wave
from pathlib import Path

import pytest
from app.core.config import settings
from app.services.external.asr import _llm_available, transcribe
from app.services.external.dashscope import moderate


# 生成 0.5s 16kHz 16bit 单声道 wav（测试音频夹具）
def _make_wav(path: Path, seconds: float = 0.5) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * int(16000 * seconds))
    return path


@pytest.fixture(autouse=True)
def _ensure_mock_mode():
    assert settings.mock_external_ai is True, "测试环境要求 MOCK_EXTERNAL_AI=true"
    yield


@pytest.fixture()
def wav_file(tmp_path: Path) -> Path:
    return _make_wav(tmp_path / "sample.wav")


# ---------- 服务层 ----------

def test_mock_fallback_deterministic(wav_file: Path):
    """未配 key（mock 模式）→ mock 兜底：确定性输出 + 同构结构"""
    r1 = transcribe(wav_file)
    r2 = transcribe(wav_file)
    assert r1.mock is True
    assert r1.channel == "mock"
    assert r1.text == "这是一段本地模拟转写文本。"
    assert r1.emotion == "平静"
    assert r1.duration_ms == 500  # wav 头解析 0.5s
    assert r1 == r2  # 确定性
    assert r1.errors  # 记录降级原因（未配置 key）


def test_mock_preferred(wav_file: Path):
    """显式 preferred=mock 直接走 mock，不依赖 key"""
    r = transcribe(wav_file, preferred="mock")
    assert r.channel == "mock"
    assert r.mock is True


def test_transcribe_missing_file():
    """音频文件不存在 → FileNotFoundError（不静默）"""
    with pytest.raises(FileNotFoundError):
        transcribe(Path("C:/nonexistent/not_here.wav"))


def test_channel_fallback_on_real_failure(wav_file: Path, monkeypatch):
    """真实模式（配 key）下 funasr 失败 → 自动降级 sensevoice → 再失败 → mock"""
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")

    import app.services.external.asr as asr_mod

    calls = []

    def fake_funasr(path):
        calls.append("funasr")
        raise RuntimeError("网络超时")

    def fake_sensevoice(path):
        calls.append("sensevoice")
        raise RuntimeError("模型不可用")

    # _CHANNELS 在模块加载时捕获函数对象，需直接替换字典项
    monkeypatch.setitem(asr_mod._CHANNELS, "funasr", fake_funasr)
    monkeypatch.setitem(asr_mod._CHANNELS, "sensevoice", fake_sensevoice)

    r = transcribe(wav_file, preferred="auto")
    assert r.channel == "mock"
    assert r.mock is True
    assert calls == ["funasr", "sensevoice"]  # 降级顺序正确
    assert len(r.errors) == 2


def test_parse_sentences_shapes():
    """_parse_sentences 兼容两种响应形态（2026-08-19 实测回归）

    dashscope 1.26.7 paraformer-realtime-v2 的 get_sentence() 返回 list[dict]；
    旧代码按 .sentence 属性取恒为空 → 误判"空转写"。两种形态都必须解析出文本。
    """
    from app.services.external.asr import _parse_sentences

    class RespList:
        """实测形态：get_sentence() 直接返回 list"""
        def get_sentence(self):
            return [{"sentence_id": 1, "text": "今天天气不错", "begin_time": 0, "end_time": 1000}]

    class RespObj:
        """旧文档形态：get_sentence() 返回带 .sentence 属性的对象"""
        def get_sentence(self):
            return type("S", (), {"sentence": [{"sentence_id": 1, "text": "明天会更好"}]})()

    class RespEmpty:
        def get_sentence(self):
            return None

    assert _parse_sentences(RespList()) == [
        {"sentence_id": 1, "text": "今天天气不错", "begin_time": 0, "end_time": 1000}
    ]
    assert _parse_sentences(RespObj()) == [{"sentence_id": 1, "text": "明天会更好"}]
    assert _parse_sentences(RespEmpty()) == []


def test_llm_available_flag(monkeypatch):
    """_llm_available：mock 模式 False；真实模式+key True"""
    assert _llm_available() is False
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    assert _llm_available() is True


# ---------- API 层 ----------

@pytest.fixture()
def client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


def _auth_headers(client) -> dict:
    """走真实 DB 登录链路取 token（对齐 test_agent api_smoke）"""
    r = client.post("/api/v1/auth/wechat", json={"code": "t1", "device_id": "d1"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["data"]["access_token"]}


def test_transcribe_api_mock(wav_file: Path, client):
    """transcribe API：wav 上传 → mock 转写 + 护栏集成（mock 放行）"""
    headers = _auth_headers(client)
    with open(wav_file, "rb") as f:
        r = client.post(
            "/api/v1/asr/transcribe",
            files={"file": ("sample.wav", f, "audio/wav")},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["channel"] == "mock"
    assert data["mock"] is True
    assert data["text"] == "这是一段本地模拟转写文本。"
    assert data["emotion"] == "平静"
    assert data["guardrail"]["passed"] is True  # mock 护栏放行


def test_transcribe_api_requires_auth(client):
    """未认证 → 401（AUTH-005）"""
    r = client.post(
        "/api/v1/asr/transcribe",
        files={"file": ("a.wav", io.BytesIO(b"RIFFxxxxWAVE"), "audio/wav")},
    )
    assert r.status_code == 401


def test_transcribe_api_rejects_non_wav(client):
    """非 wav 魔数 → ASR_001 拒绝"""
    headers = _auth_headers(client)
    r = client.post(
        "/api/v1/asr/transcribe",
        files={"file": ("a.txt", io.BytesIO(b"not audio"), "text/plain")},
        headers=headers,
    )
    assert r.status_code == 422  # 审查修复(P1-08)：错误不再伪装 200，统一 4xx ApiError
    body = r.json()
    assert body["code"] == "ASR_001"
    assert "wav" in body["message"]


def test_transcribe_api_rejects_empty(client):
    """空文件 → ASR_001 拒绝"""
    headers = _auth_headers(client)
    r = client.post(
        "/api/v1/asr/transcribe",
        files={"file": ("empty.wav", io.BytesIO(b""), "audio/wav")},
        headers=headers,
    )
    assert r.status_code == 422  # 审查修复(P1-08)：错误不再伪装 200
    assert r.json()["code"] == "ASR_001"


def test_guard_check_api(client):
    """guard/check：mock 放行 + 认证保护"""
    headers = _auth_headers(client)
    r = client.post("/api/v1/guard/check", json={"text": "今天天气不错"}, headers=headers)
    assert r.status_code == 200
    data = r.json()["data"]
    assert data["passed"] is True

    # 未认证 → 401
    r2 = client.post("/api/v1/guard/check", json={"text": "测试"})
    assert r2.status_code == 401


def test_guard_fail_safe_on_real_failure(monkeypatch):
    """真实模式百炼不可用 → 护栏拦截（fail-safe，决策 #12）"""
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    # 显式 mock 百炼调用失败（2026-08-20 修复：改 qwen-flash 后真实环境
    # 下 SDK 从 env 读 key 绕过 settings，假 key 不再必然失败）
    import app.services.external.dashscope as ds_mod

    def boom(system, user, model="qwen-flash"):
        raise RuntimeError("百炼不可用（模拟）")

    monkeypatch.setattr(ds_mod, "_chat_text", boom)
    verdict = moderate("测试内容")
    assert verdict["pass"] is False
    assert verdict["reason"] == "guard-unavailable"


# ---- 长录音 VAD 分段（B5a-2 三档策略 · 审查修复 P1-16）----


def _make_long_wav(path: Path, seconds: float, with_speech: bool = True) -> Path:
    """生成指定时长 wav：with_speech=True 时前 10s 为有语音帧（非全静音）"""
    import struct

    rate = 16000
    frames = int(rate * seconds)
    # 生成 1kHz 方波作为"语音"（VAD 可检测），静音为全零
    speech_frames = int(rate * 10)
    buf = bytearray()
    for i in range(frames):
        if with_speech and i < speech_frames:
            v = 8000 if (i // 80) % 2 == 0 else -8000
            buf += struct.pack("<h", v)
        else:
            buf += struct.pack("<h", 0)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(bytes(buf))
    return path


def test_vad_segments_splits_long_audio(tmp_path):
    """>5min 长录音 → 产生分段（含语音段）"""
    from app.services.external.asr import _segments_for

    wav = _make_long_wav(tmp_path / "long.wav", seconds=360)
    segs = _segments_for(wav)
    assert segs, "6 分钟含语音音频应产生分段"
    # 每段时长在合理范围（≤5min 上限）
    for start_ms, end_ms in segs:
        assert end_ms - start_ms <= 301_500, f"段超长: {end_ms - start_ms}ms"


def test_vad_no_segments_for_short_audio(tmp_path):
    """≤5min 音频不分段（整段转写）"""
    from app.services.external.asr import _segments_for

    wav = _make_long_wav(tmp_path / "short.wav", seconds=120)
    assert _segments_for(wav) is None


def test_vad_all_silence_no_segments(tmp_path):
    """全静音音频 → 无有效分段（返回 None，走整段 mock 兜底）"""
    from app.services.external.asr import _segments_for

    wav = _make_long_wav(tmp_path / "silence.wav", seconds=360, with_speech=False)
    assert _segments_for(wav) is None


def test_transcribe_long_audio_merges_segments(tmp_path, monkeypatch):
    """长录音转写：分段转写合并（mock 通道验证合并行为）"""
    from app.services.external.asr import transcribe

    wav = _make_long_wav(tmp_path / "long2.wav", seconds=360)
    monkeypatch.setattr("app.services.external.asr._llm_available", lambda: False)
    result = transcribe(str(wav), preferred="auto")
    assert result.text  # 非空（mock 文本拼接）
    assert result.mock is True
