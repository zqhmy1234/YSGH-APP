"""CLI/辅助脚本纯函数最小单测（H3/R8#11 2026-08-27）

侦察：event_aggregation 三脚本已由 R8#10/#11 补测（test_event_aggregation_scripts.py）；
本文件补 `scripts/` 与 `backend/scripts/` 里**可 import 的纯函数**的最小单测——
覆盖规则引擎/评测/沙箱/迁移门禁等无外部依赖的判定逻辑，避免长期 0% 误导覆盖率报告。

显式豁免（不补测，理由）：
  - train_setfit / prepare_sensevoice：需真实模型训练/下载（重量级副作用），仅保留 skipif 守卫
  - check_dashscope / smoke_cos / smoke_cos_upload / api_smoke_cases / warm_hf_models：
    需真实外部服务与密钥（属集成冒烟，走 CI 单独步骤，不入单测）
  - backfill_thumbnails / daily_review / measure_correction_gain：需 DB + 存储后端全链路
  - gen_agg_fixtures：核心聚合由 test_event_aggregation_scripts.py 覆盖，local_ms 属纯时间工具
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ 与 backend/scripts/ 非包内路径，需显式加 sys.path 后按模块 import
_ROOT = Path(__file__).resolve().parent.parent.parent
_BACKEND = _ROOT / "backend"
for _p in (_ROOT, _BACKEND):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# wecom_sandbox（backend/scripts/wecom_sandbox.py）：企微回调沙箱协议构造
# ---------------------------------------------------------------------------


def test_wecom_xml_wrap():
    """_xml_wrap：Encrypt CDATA 包裹（回调报文外层结构）"""
    from scripts.wecom_sandbox import _xml_wrap

    out = _xml_wrap("ABC")
    assert out == "<xml><Encrypt><![CDATA[ABC]]></Encrypt></xml>"


def test_wecom_text_message_xml_fields():
    """_text_message_xml：文本消息含 Content/MsgId/MsgType 关键字段"""
    from scripts.wecom_sandbox import _text_message_xml

    xml = _text_message_xml("明天取快递", "msg-123")
    assert "明天取快递" in xml
    assert "msg-123" in xml
    assert "<MsgType><![CDATA[text]]></MsgType>" in xml


def test_wecom_image_voice_message_fields():
    """image/voice 消息：MediaId 与 MsgType 正确（协议字段契约）"""
    from scripts.wecom_sandbox import _image_message_xml, _voice_message_xml

    img = _image_message_xml("media-img", "msg-i")
    assert "<MsgType><![CDATA[image]]></MsgType>" in img
    assert "media-img" in img
    voice = _voice_message_xml("media-v", "msg-v")
    assert "<MsgType><![CDATA[voice]]></MsgType>" in voice
    assert "media-v" in voice


def test_wecom_build_callback_request_shape():
    """build_callback_request：body/params(msg_signature/timestamp/nonce)/msg_id 结构齐全"""
    from scripts.wecom_sandbox import build_callback_request

    req = build_callback_request("text", "你好")
    assert set(req) == {"body", "params", "msg_id"}
    assert set(req["params"]) == {"msg_signature", "timestamp", "nonce"}
    assert len(str(req["msg_id"])) <= 15
    with pytest.raises(ValueError):
        build_callback_request("unknown-type", "x")


def test_wecom_build_verify_request_shape():
    """build_verify_request：URL 验证 echostr 加密 + expected_plain 回读"""
    from scripts.wecom_sandbox import build_verify_request

    req = build_verify_request("1616140317555161061")
    assert req["expected_plain"] == "1616140317555161061"
    assert set(req["params"]) == {"msg_signature", "timestamp", "nonce", "echostr"}


# ---------------------------------------------------------------------------
# run_wer_bench（scripts/run_wer_bench.py）：WER/CER 评测纯函数
# ---------------------------------------------------------------------------


def test_wer_clean_strips_punct_and_space():
    """_clean：去标点/空白（中文标点归一，转写与标注对齐）"""
    from scripts.run_wer_bench import _clean

    assert _clean("明天，去。取快递！") == "明天去取快递"
    assert _clean("hello world") == "helloworld"


def test_cer_exact_and_substitution():
    """_cer：字级编辑距离——完全相同=0；单字替换 < 1；空参考特殊语义"""
    from scripts.run_wer_bench import _cer

    assert _cer("明天取快递", "明天取快递") == 0.0
    assert _cer("明天去快递", "明天取快递") == 1 / 5  # 1 字替换 / 5 字
    assert _cer("", "") == 0.0
    assert _cer("", "有输出") == 1.0


def test_cer_ignores_punct_diff():
    """_cer：仅标点差异 → 0（归一后等价）"""
    from scripts.run_wer_bench import _cer

    assert _cer("你好，世界。", "你好世界") == 0.0


# ---------------------------------------------------------------------------
# build_truth_corpus（scripts/build_truth_corpus.py）：评测语料稳定 UUID
# ---------------------------------------------------------------------------


def test_truth_corpus_stable_uuid_deterministic():
    """_stable_uuid：同 batch+collect_id 稳定一致（重跑不漂移）"""
    from scripts.build_truth_corpus import _stable_uuid

    a = _stable_uuid("b", "frag-1")
    b = _stable_uuid("b", "frag-1")
    assert a == b
    assert _stable_uuid("a", "frag-1") != a  # 不同 batch 不同 uuid


# ---------------------------------------------------------------------------
# reflow_global（backend/scripts/reflow_global.py）：模型迁移门禁
# ---------------------------------------------------------------------------


def test_reflow_backup_skips_when_no_model(tmp_path):
    """_backup_model：无生产模型 → 跳过返回 None（首次训练不误备份）"""
    from scripts.reflow_global import _backup_model

    assert _backup_model(tmp_path / "missing", tmp_path / "backup") is None


def test_reflow_promote_gate_blocks_below_threshold(tmp_path):
    """_promote_if_gate：评估门禁不达标 → 保留原模型并抛错（staging 清理）"""
    import scripts.reflow_global as rg

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "model.safetensors").write_bytes(b"m")
    model_dir = tmp_path / "prod"
    model_dir.mkdir()
    with pytest.raises(RuntimeError, match="门禁未过"):
        rg._promote_if_gate(staging, model_dir, acc=0.5, gate=0.75)
    assert (model_dir / "model.safetensors").exists() is False  # 原模型未被覆盖
    assert staging.exists() is False  # staging 已清理


def test_reflow_promote_gate_passes_and_replaces(tmp_path):
    """_promote_if_gate：达标 → staging 覆盖生产模型"""
    import scripts.reflow_global as rg

    staging = tmp_path / "staging"
    staging.mkdir()
    (staging / "model.safetensors").write_bytes(b"new")
    model_dir = tmp_path / "prod"
    model_dir.mkdir()
    rg._promote_if_gate(staging, model_dir, acc=0.8, gate=0.75)
    assert (model_dir / "model.safetensors").read_bytes() == b"new"
    assert staging.exists() is False


# ---------------------------------------------------------------------------
# gen_agg_fixtures（scripts/gen_agg_fixtures.py）：聚合夹具本地时间戳
# ---------------------------------------------------------------------------


def test_gen_agg_local_ms_is_epoch_utc():
    """local_ms：本地时间 → UTC epoch 毫秒（与端侧时间轴契约一致）"""
    from scripts.gen_agg_fixtures import local_ms

    ms = local_ms(2026, 8, 1, 12, 0)
    assert isinstance(ms, int) and ms > 1_700_000_000_000
