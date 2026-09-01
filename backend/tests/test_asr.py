"""ASR 多格式 + 状态语义 + 护栏 API 测试（无真实 Key、零费用）。

覆盖：
  - 服务层：开发 mock；真实模式失败不伪造完成；数字静音 no_speech
  - Fun-ASR Flash：M4A payload、供应商响应、重试分类（monkeypatch 不联网）
  - API 层：M4A/WAV 上传校验、认证保护、明确错误与护栏集成
  - 护栏：guard/check 放行 + fail-safe 拦截语义
"""
import io
import wave
from pathlib import Path

import pytest
from app.core.config import settings
from app.services.external.asr import AsrError, AsrResult, transcribe
from app.services.external.dashscope import moderate


# 生成 0.5s 16kHz 16bit 单声道 wav（测试音频夹具）
def _make_wav(path: Path, seconds: float = 0.5, *, silence: bool = False) -> Path:
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        sample = b"\x00\x00" if silence else b"\xe8\x03"
        wf.writeframes(sample * int(16000 * seconds))
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
    assert r1.emotion is None  # D-16: mock 不再伪造「平静」
    assert r1.duration_ms == 500  # wav 头解析 0.5s
    assert r1 == r2  # 确定性
    assert r1.errors  # 记录降级原因（未配置 key）


def test_mock_preferred(wav_file: Path):
    """显式 preferred=mock 直接走 mock，不依赖 key"""
    r = transcribe(wav_file, preferred="mock")
    assert r.channel == "mock"
    assert r.mock is True


def test_unmeasured_emotion_is_none_not_calm():
    """D-16 回归：未测得情绪=None（而非「平静」）；audit 的 actionable 同步不放行"""
    r = AsrResult(text="t", channel="funasr")
    assert r.emotion is None
    assert r.emotion_source == "none"
    assert r.audit_dict()["emotion_actionable"] is False


def test_production_rejects_global_mock_mode(wav_file: Path, monkeypatch):
    """生产环境即使误开全局 mock，也必须显式失败而非返回假转写。"""
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mock_external_ai", True)

    with pytest.raises(AsrError) as raised:
        transcribe(wav_file)

    assert raised.value.code == "MOCK_DISABLED"
    assert raised.value.outcome == "failed_final"


def test_workspace_base_url_uses_configured_region(monkeypatch):
    from app.services.external.asr import _dashscope_base_url

    monkeypatch.delenv("DASHSCOPE_BASE_URL", raising=False)
    monkeypatch.setattr(settings, "dashscope_workspace_id", "ws-example")
    monkeypatch.setattr(settings, "dashscope_region", "cn-shanghai")
    assert _dashscope_base_url() == (
        "https://ws-example.cn-shanghai.maas.aliyuncs.com/api/v1"
    )


def test_explicit_dashscope_base_url_overrides_region(monkeypatch):
    from app.services.external.asr import _dashscope_base_url

    monkeypatch.setenv("DASHSCOPE_BASE_URL", "https://custom.example/api/v1/")
    monkeypatch.setattr(settings, "dashscope_workspace_id", "ws-example")
    monkeypatch.setattr(settings, "dashscope_region", "cn-shanghai")
    assert _dashscope_base_url() == "https://custom.example/api/v1"


def test_production_requires_preloaded_sensevoice(monkeypatch):
    from app.services.external.asr import _sensevoice_model_dir

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "sensevoice_model_dir", "")
    with pytest.raises(AsrError) as raised:
        _sensevoice_model_dir()
    assert raised.value.code == "SENSEVOICE_MODEL_NOT_PRELOADED"


def test_prepare_sensevoice_assets_downloads_and_validates(tmp_path):
    from app.services.external import asr as asr_mod

    target = tmp_path / "sensevoice"
    tokenizer_dir = tmp_path / "tokenizer"

    def fake_snapshot(model_id, **kwargs):
        if model_id == asr_mod.MODEL_SENSEVOICE:
            model_dir = Path(kwargs["local_dir"])
            model_dir.mkdir(parents=True, exist_ok=True)
            (model_dir / "model_quant.onnx").write_bytes(b"onnx")
            return str(model_dir)
        tokenizer_dir.mkdir(parents=True, exist_ok=True)
        (tokenizer_dir / asr_mod._SENSEVOICE_TOKENIZER_NAME).write_bytes(b"spm")
        return str(tokenizer_dir)

    resolved = asr_mod.prepare_sensevoice_assets(
        target,
        snapshot_download_fn=fake_snapshot,
    )
    assert resolved == target.resolve()
    assert (resolved / "model_quant.onnx").is_file()
    assert (resolved / asr_mod._SENSEVOICE_TOKENIZER_NAME).is_file()


def test_primary_emotion_skips_local_enhancement(monkeypatch):
    from app.services.external import asr as asr_mod

    result = AsrResult(
        text="主通道已返回情绪",
        channel="funasr",
        emotion="开心",
        emotion_confidence=0.8,
        emotion_source="funasr",
    )

    def should_not_run(path):
        raise AssertionError("不应调用本地模型")

    monkeypatch.setattr(asr_mod, "_infer_sensevoice", should_not_run)
    enhanced = asr_mod._enhance_with_local_emotion(
        result,
        Path("unused.wav"),
        mode="auto",
    )
    assert enhanced is result
    assert enhanced.emotion_source == "funasr"


def test_transcribe_missing_file():
    """音频文件不存在 → 结构化 ASR 错误（不静默）"""
    with pytest.raises(AsrError) as raised:
        transcribe(Path("C:/nonexistent/not_here.wav"))
    assert raised.value.code == "AUDIO_NOT_FOUND"


def test_channel_fallback_on_real_failure(wav_file: Path, monkeypatch):
    """真实模式双通道均失败 → 显式失败，绝不返回 mock 假文本。"""
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

    with pytest.raises(AsrError) as raised:
        transcribe(wav_file, preferred="auto")
    assert raised.value.code == "ASR_UNAVAILABLE"
    assert raised.value.retryable is True
    assert calls == ["funasr", "sensevoice"]  # 降级顺序正确
    assert len(raised.value.errors) == 2


def test_real_mode_missing_key_is_explicit_failure(wav_file: Path, monkeypatch):
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    with pytest.raises(AsrError) as raised:
        transcribe(wav_file)
    assert raised.value.code == "MISSING_API_KEY"
    assert raised.value.outcome == "failed_final"


def test_real_mode_digital_silence_is_no_speech(tmp_path: Path, monkeypatch):
    silent = _make_wav(tmp_path / "silent.wav", silence=True)
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "")
    result = transcribe(silent)
    assert result.outcome == "no_speech"
    assert result.channel == "local_vad"
    assert result.text == ""


def test_mock_mode_digital_silence_does_not_create_fake_text(tmp_path: Path):
    silent = _make_wav(tmp_path / "silent-mock.wav", silence=True)
    result = transcribe(silent)
    assert result.outcome == "no_speech"
    assert result.mock is False
    assert result.text == ""


def test_funasr_flash_accepts_m4a_and_keeps_audit_fields(tmp_path: Path, monkeypatch):
    from app.services.external import asr as asr_mod
    from app.services.external.asr import SenseVoiceResult

    m4a = tmp_path / "phone.m4a"
    m4a.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"audio-data")
    captured = {}

    def fake_post(url, api_key, payload, timeout_seconds=180.0):
        captured.update({"url": url, "api_key": api_key, "payload": payload})
        return 200, {
            "request_id": "req-test-1",
            "output": {"text": "真实格式测试", "sentence": {"confidence": 0.91}},
            "usage": {"duration": 1.25},
        }

    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(asr_mod, "_http_post_json", fake_post)
    monkeypatch.setattr(asr_mod, "_call_with_retry", lambda call: call())
    monkeypatch.setattr(
        asr_mod,
        "_infer_sensevoice",
        lambda path: SenseVoiceResult(
            text="本地辅助文本",
            emotion="开心",
            emotion_confidence=0.86,
            raw_emotion="<|HAPPY|>",
        ),
    )

    result = transcribe(m4a)
    assert result.outcome == "succeeded"
    assert result.text == "真实格式测试"
    assert result.audio_format == "m4a"
    assert result.model == "fun-asr-flash-2026-06-15"
    assert result.emotion == "开心"
    assert result.emotion_confidence == 0.86
    assert result.emotion_source == "sensevoice_local"
    assert result.emotion_model == "iic/SenseVoiceSmall-onnx"
    assert result.provider_request_id == "req-test-1"
    assert result.source_audio_sha256
    assert captured["payload"]["parameters"]["format"] == "m4a"
    audio_data = captured["payload"]["input"]["messages"][0]["content"][0]["input_audio"]["data"]
    assert audio_data.startswith("data:audio/mp4;base64,")


def test_local_emotion_failure_keeps_successful_cloud_transcript(tmp_path: Path, monkeypatch):
    from app.services.external import asr as asr_mod

    m4a = tmp_path / "phone.m4a"
    m4a.write_bytes(b"\x00\x00\x00\x18ftypM4A " + b"audio-data")
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(
        asr_mod,
        "_http_post_json",
        lambda *args, **kwargs: (200, {"output": {"text": "云端已成功"}}),
    )
    monkeypatch.setattr(asr_mod, "_call_with_retry", lambda call: call())

    def fail_emotion(path):
        raise AsrError("SENSEVOICE_INFERENCE_FAILED", "model failed")

    monkeypatch.setattr(asr_mod, "_infer_sensevoice", fail_emotion)

    result = transcribe(m4a)
    assert result.outcome == "succeeded"
    assert result.text == "云端已成功"
    assert result.emotion_source == "none"
    assert "sensevoice_emotion:SENSEVOICE_INFERENCE_FAILED" in result.errors


def test_sensevoice_emotion_confidence_uses_emotion_logits():
    import numpy as np
    from app.services.external import asr as asr_mod

    tags = [
        *asr_mod.SENSEVOICE_EMOTION_TAGS,
        asr_mod.SENSEVOICE_UNKNOWN_EMOTION_TAG,
    ]
    token_ids = {tag: index for index, tag in enumerate(tags)}

    class FakeSentencePiece:
        @staticmethod
        def PieceToId(tag):
            return token_ids[tag]

    model = type(
        "Model",
        (),
        {"tokenizer": type("Tokenizer", (), {"sp": FakeSentencePiece()})()},
    )()
    logits = np.zeros((2, len(tags)), dtype=np.float32)
    logits[1, token_ids["<|HAPPY|>"]] = 2.0

    confidence = asr_mod._sensevoice_emotion_confidence(
        model,
        logits,
        "<|HAPPY|>",
    )
    assert confidence == pytest.approx(0.513519, rel=1e-5)


def test_retry_only_for_retryable_provider_error(monkeypatch):
    from app.services.external.asr import _call_with_retry

    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise AsrError("NETWORK_ERROR", "timeout", retryable=True)
        return 200, {"output": {"text": "ok"}}

    code, _ = _call_with_retry(flaky, retries=2, sleep=lambda _: None)
    assert code == 200
    assert len(calls) == 3


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
    assert data["outcome"] == "mock"
    assert data["mock"] is True
    assert data["text"] == "这是一段本地模拟转写文本。"
    assert data["emotion"] is None  # D-16: 未测得 -> API 返回 null
    assert data["guardrail"]["passed"] is True  # mock 护栏放行


def test_transcribe_api_accepts_m4a_in_mock_mode(client):
    headers = _auth_headers(client)
    payload = b"\x00\x00\x00\x18ftypM4A " + b"audio-data"
    r = client.post(
        "/api/v1/asr/transcribe",
        files={"file": ("phone.m4a", io.BytesIO(payload), "audio/mp4")},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["audio_format"] == "m4a"
    assert data["outcome"] == "mock"


def test_transcribe_api_requires_auth(client):
    """未认证 → 401（AUTH-005）"""
    r = client.post(
        "/api/v1/asr/transcribe",
        files={"file": ("a.wav", io.BytesIO(b"RIFFxxxxWAVE"), "audio/wav")},
    )
    assert r.status_code == 401


def test_transcribe_api_rejects_non_audio(client):
    """不支持的扩展名/魔数 → ASR_001 拒绝。"""
    headers = _auth_headers(client)
    r = client.post(
        "/api/v1/asr/transcribe",
        files={"file": ("a.txt", io.BytesIO(b"not audio"), "text/plain")},
        headers=headers,
    )
    assert r.status_code == 422  # 审查修复(P1-08)：错误不再伪装 200，统一 4xx ApiError
    body = r.json()
    assert body["code"] == "ASR_001"
    assert "不支持" in body["message"]


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


def test_transcribe_api_provider_failure_is_503(wav_file: Path, client, monkeypatch):
    headers = _auth_headers(client)

    def unavailable(*args, **kwargs):
        raise AsrError("NETWORK_ERROR", "语音服务暂不可用", retryable=True)

    monkeypatch.setattr("app.api.asr.transcribe", unavailable)
    with wav_file.open("rb") as stream:
        r = client.post(
            "/api/v1/asr/transcribe",
            files={"file": ("sample.wav", stream, "audio/wav")},
            headers=headers,
        )
    assert r.status_code == 503
    assert r.json()["details"]["outcome"] == "failed_retryable"


def test_transcribe_api_no_speech_is_explicit(wav_file: Path, client, monkeypatch):
    headers = _auth_headers(client)
    result = AsrResult(
        text="",
        channel="local_vad",
        outcome="no_speech",
        model="digital-silence-v1",
        provider="local",
        audio_format="wav",
        source_audio_sha256="abc",
    )
    monkeypatch.setattr("app.api.asr.transcribe", lambda *args, **kwargs: result)
    with wav_file.open("rb") as stream:
        r = client.post(
            "/api/v1/asr/transcribe",
            files={"file": ("sample.wav", stream, "audio/wav")},
            headers=headers,
        )
    assert r.status_code == 200, r.text
    data = r.json()["data"]
    assert data["outcome"] == "no_speech"
    assert data["text"] == ""
    assert data["guardrail"] == {"passed": True, "reason": "no-speech"}


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


# R8#5（2026-08-27）：VAD 三连改表驱动（保留中文场景语义到 parametrize id）。
# expectation：None=不分段（≤4min）；[]=长静音空分段；"segments"=产生分段且每段 ≤4min
VAD_SEGMENT_CASES = [
    (360, True, "segments"),
    (120, True, None),
    (360, False, []),
]


@pytest.mark.parametrize(
    ("seconds", "with_speech", "expected"),
    VAD_SEGMENT_CASES,
    ids=["6分钟含语音长录音分段", "≤4分钟音频不分段", "长静音空分段→no_speech"],
)
def test_vad_segments(seconds, with_speech, expected, tmp_path):
    """>4min 含语音 → 分段（每段 ≤4min）；≤4min → 不分段；长静音 → 空分段"""
    from app.services.external.asr import _segments_for

    wav = _make_long_wav(tmp_path / "vad.wav", seconds=seconds, with_speech=with_speech)
    segs = _segments_for(wav)
    if expected is None:
        assert segs is None
    elif expected == []:
        assert segs == []
    else:
        assert segs, "6 分钟含语音音频应产生分段"
        for start_ms, end_ms in segs:
            assert end_ms - start_ms <= 241_500, f"段超长: {end_ms - start_ms}ms"


def test_transcribe_long_audio_merges_segments(tmp_path, monkeypatch):
    """长录音转写：分段转写合并（mock 通道验证合并行为）"""
    from app.services.external.asr import transcribe

    wav = _make_long_wav(tmp_path / "long2.wav", seconds=360)
    result = transcribe(str(wav), preferred="auto")
    assert result.text  # 非空（mock 文本拼接）
    assert result.mock is True
    # J-3：合并结构存在（mock 段情绪同构，dominant==peak==平静）
    assert result.emotion_merge is not None
    assert result.emotion_merge["strategy"] == "longest_dominant_peak"
    assert result.emotion == result.emotion_merge["dominant"]["emotion"]
    assert len(result.emotion_merge["segments"]) >= 1


# ---------- B5a Wave4 AgentJ：音频事件 / 噪音降权 / 段级情绪合并 ----------


# R8#5（2026-08-27）：audio-event 组改表驱动（中文场景语义保留在 parametrize id）
AUDIO_EVENT_PARSE_CASES = [
    ("<|NEUTRAL|><|Speech|>", []),
    ("<|EMO_UNKNOWN|>", []),
    ("<|LAUGHTER|>", ["laughter"]),
    ("<|giggle|>", ["laughter"]),
    ("<|LAUGHTER|><|BGM|><|NOISE|>", ["laughter", "environment"]),
    ("<|SILENCE|>", ["silence"]),
    ("<|LAUGHTER|><|Laughter|>", ["laughter"]),
]


@pytest.mark.parametrize(
    ("raw", "expected"),
    AUDIO_EVENT_PARSE_CASES,
    ids=[
        "正常语音标签不消费",
        "未知情绪标签不消费",
        "笑声标签LAUGHTER",
        "大小写不敏感-giggle",
        "环境音事件BGM噪声",
        "静音标签SILENCE",
        "同事件去重",
    ],
)
def test_parse_audio_events(raw, expected):
    """J-1：SenseVoice 富文本标签 → 音频事件 3 类（笑声/静音/键盘环境音）"""
    from app.services.external.asr import _parse_audio_events

    assert _parse_audio_events(raw) == expected


AUDIO_EVENT_EFFECT_CASES = [
    # (base_emotion, base_conf, events, exp_emotion, exp_bonus, exp_silence, exp_not_oral, exp_source)
    ("平静", 0.3, ["laughter"], "开心", True, False, False, "audio_event_laughter"),
    (None, 0.0, ["laughter"], "开心", True, False, False, "audio_event_laughter"),
    ("难过", 0.9, ["laughter"], "难过", True, False, False, "none"),
    ("平静", 0.3, ["silence"], "平静", False, True, False, "none"),
    ("平静", 0.3, ["environment"], "平静", False, False, True, "none"),
]


@pytest.mark.parametrize(
    ("base_emotion", "base_conf", "events", "exp_emotion", "exp_bonus", "exp_silence", "exp_not_oral", "exp_source"),
    AUDIO_EVENT_EFFECT_CASES,
    ids=[
        "笑声-平静提升为开心", "笑声-未测得(None)提升为开心",
        "笑声-不覆盖强负向情绪", "静音-空段提示", "环境音-疑似非口述",
    ],
)
def test_audio_event_effects(
    base_emotion,
    base_conf,
    events,
    exp_emotion,
    exp_bonus,
    exp_silence,
    exp_not_oral,
    exp_source,
):
    """J-1：apply_audio_event_effects —— 笑声加分 / 静音空段 / 环境音非口述"""
    from app.services.external.asr import AsrResult, apply_audio_event_effects

    r = AsrResult(text="t", channel="mock", emotion=base_emotion, emotion_confidence=base_conf, mock=True)
    r.audio_events = list(events)
    apply_audio_event_effects(r)
    assert r.emotion == exp_emotion
    assert r.emotion_bonus is exp_bonus
    assert r.silence_hint is exp_silence
    assert r.not_oral is exp_not_oral
    assert r.emotion_source == exp_source
    if "laughter" in events:
        assert r.emotion_confidence >= 0.6


def test_sensevoice_transcribe_propagates_audio_events(tmp_path, monkeypatch):
    """J-1：SenseVoice 通道把本地推理的音频事件传播到 AsrResult"""
    from app.services.external import asr as asr_mod
    from app.services.external.asr import SenseVoiceResult

    wav = _make_wav(tmp_path / "sv.wav")
    monkeypatch.setattr(
        asr_mod,
        "_infer_sensevoice",
        lambda path: SenseVoiceResult(
            text="哈",
            emotion="平静",
            emotion_confidence=0.2,
            raw_emotion="<|NEUTRAL|>",
            audio_events=["laughter", "environment"],
        ),
    )
    result = asr_mod._transcribe_sensevoice(wav)
    assert result.audio_events == ["laughter", "environment"]
    # 消费：笑声加分 → 开心；环境音 → 疑似非口述
    assert result.emotion_bonus is True
    assert result.emotion == "开心"
    assert result.not_oral is True


def test_estimate_snr_clean_vs_noisy(tmp_path):
    """J-2：轻量 SNR —— 干净语音（有静音间隙）→ 高 SNR；白噪声 → 低 SNR（降权）"""
    import wave

    from app.services.external.asr import (
        NOISE_SNR_THRESHOLD_DB,
        _noise_weight,
        estimate_snr,
    )

    # 干净语音：语音帧 + 大量静音 → 底噪低 → SNR 高
    clean = _make_long_wav(tmp_path / "clean.wav", seconds=60)
    snr_clean = estimate_snr(clean)
    assert snr_clean is not None
    assert snr_clean >= NOISE_SNR_THRESHOLD_DB
    assert _noise_weight(snr_clean) == "high"

    # 白噪声（环境音主导）→ 底噪≈信号 → SNR 低 → 声学权重降为持平
    import numpy as np

    rate = 16000
    frames = int(rate * 30)
    noise_samples = np.random.default_rng(42).integers(
        -3000, 3000, size=frames, dtype=np.int16
    )
    noisy = tmp_path / "noisy.wav"
    with wave.open(str(noisy), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(noise_samples.tobytes())
    snr_noisy = estimate_snr(noisy)
    assert snr_noisy is not None
    assert snr_noisy < NOISE_SNR_THRESHOLD_DB
    assert _noise_weight(snr_noisy) == "equal"

    # 非 16bit 单声道 / 全零 → None（不参与降权）
    assert estimate_snr(_make_wav(tmp_path / "s.wav", silence=True)) is None


def test_merge_segment_emotion_dominant_longest_peak_max():
    """J-3：段级合并 —— 主导=时长最长段；峰值=置信度最高段（保留标记）"""
    from app.services.external.asr import AsrResult, merge_segment_emotion

    results = [
        AsrResult(text="a", channel="mock", emotion="平静", emotion_confidence=0.2, duration_ms=120_000, mock=True),
        AsrResult(text="b", channel="mock", emotion="难过", emotion_confidence=0.9, duration_ms=30_000, mock=True),
        AsrResult(text="c", channel="mock", emotion="开心", emotion_confidence=0.6, duration_ms=60_000, mock=True),
    ]
    m = merge_segment_emotion(results, noise_weight="equal")
    assert m["strategy"] == "longest_dominant_peak"
    assert m["dominant"]["emotion"] == "平静"      # 时长最长段（120s）
    assert m["dominant"]["segment_index"] == 1
    assert m["dominant"]["duration_ms"] == 120_000
    assert m["peak"]["emotion"] == "难过"          # 峰值（conf 0.9）
    assert m["peak"]["segment_index"] == 2
    assert m["noise_weight"] == "equal"
    assert len(m["segments"]) == 3
    # 峰值保留：4 分钟平静 + 30 秒哽咽 → 主导平静 + 峰值哽咽（B5a §3 示例）
    m2 = merge_segment_emotion(
        [
            AsrResult(text="x", channel="mock", emotion="平静", emotion_confidence=0.4, duration_ms=240_000, mock=True),
            AsrResult(text="y", channel="mock", emotion="难过", emotion_confidence=0.95, duration_ms=30_000, mock=True),
        ],
        noise_weight="high",
    )
    assert m2["dominant"]["emotion"] == "平静"
    assert m2["peak"]["emotion"] == "难过"


def test_single_segment_emotion_merge():
    """J-3：非分段路径 —— dominant == peak == 自身"""
    from app.services.external.asr import AsrResult, single_segment_emotion_merge

    r = AsrResult(text="t", channel="mock", emotion="难过", emotion_confidence=0.8, duration_ms=60_000, mock=True)
    m = single_segment_emotion_merge(r, noise_weight="high")
    assert m["strategy"] == "single_segment"
    assert m["dominant"]["emotion"] == "难过"
    assert m["peak"]["emotion"] == "难过"
    assert m["dominant"]["segment_index"] == 1
    assert m["noise_weight"] == "high"


def test_asr_channels_visibility(client):
    """D-09 遗留（08-29 拍板）：登录态 /api/v1/asr/channels 单腿可见性——
    只回布尔/在否，绝不泄漏 key；/healthz 最小信息纪律不受影响。"""
    from app.api import deps
    from app.main import app

    # 端点不消费 user 实体，覆盖依赖即过鉴权门（首跑 401 AUTH_005=无凭据，
    # 恰证该端点默认要求登录态）
    app.dependency_overrides[deps.get_current_user] = lambda: None
    try:
        r = client.get("/api/v1/asr/channels")
    finally:
        app.dependency_overrides.clear()
    assert r.status_code == 200, r.text
    d = r.json()["data"]
    assert set(d) == {"funasr", "sensevoice"}
    assert d["funasr"]["type"] == "remote"
    assert isinstance(d["funasr"]["ready"], bool)
    sv = d["sensevoice"]
    assert sv["type"] == "local"
    assert isinstance(sv["runtime_installed"], bool)
    assert isinstance(sv["model_dir_configured"], bool)
    assert isinstance(sv["model_dir_exists"], bool)
    body = r.text
    assert "sk-" not in body and "dashscope_api_key" not in body
