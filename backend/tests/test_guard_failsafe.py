"""护栏 fail-safe 收口（功能修复波 · 域⑩ D10-1/2/3/5/12）回归测试。

缺陷原貌（域⑩ 深审）：
  - **D10-1（P0）**：`dashscope.moderate` 的 fail-closed 分支返回
    `{"pass": False, "reason": "guard-unavailable"}` —— **不带 `action` 键**；而两个
    入库调用点按 `action` 分派 ⇒ 两条 `if` 均不成立 ⇒ **护栏明确判"拒发"却原文入库**。
  - **D10-2（P0）**：`qwen_response_check` 对 200 + 空 `content` 判 `pass=True`
    （同函数对非 200/审查拦截均抛错兜底，只有这里 fail-open 放行）。
  - **D10-3（P1）**：4 条入库/审核主链直连 `external.dashscope`，绕过策略选择器
    ⇒ "百炼托管护栏优先"实际只在回响一条链生效。
  - **D10-5（P1）**：回流词只写进**软**事件级表，而声明称"自动入规则表"
    ⇒ 被 LLM 拦过的违规词下一轮仍要再调一次 LLM。
  - **D10-12（P2）**：ASR 忽略 `masked_text`，号码/身份证以**原文**回传客户端。

前置：PG yishu 库（conftest 强制 fake 存储 + MOCK_EXTERNAL_AI=true）。
"""
from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from app.core.config import settings

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# D10-1：fail-closed 判定必须带 action；调用方不得因缺键放行
# ---------------------------------------------------------------------------


def test_fail_closed_verdict_carries_reject_action(monkeypatch):
    """生产 + 未配 key ⇒ dashscope.moderate 的 fail-closed 返回值必须 `action=reject`。"""
    from app.services.external import dashscope
    from app.services.llm_ops.moderate import verdict_action

    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "dashscope_api_key", "")

    verdict = dashscope.moderate("今天天气不错")
    assert verdict["pass"] is False
    assert verdict["reason"] == "guard-unavailable"
    assert verdict["action"] == "reject", "fail-closed 必须显式 reject（D10-1）"
    assert verdict_action(verdict) == "reject"


def test_verdict_action_is_fail_safe_on_missing_action_key():
    """`verdict_action` 的判据是 `pass` 优先：**缺 `action` 键也判拒发**（修复点本身）。"""
    from app.services.llm_ops.moderate import is_rejected, verdict_action

    assert verdict_action({"pass": False, "reason": "guard-unavailable"}) == "reject"
    assert is_rejected({"pass": False, "reason": "guard-unavailable"}) is True
    assert verdict_action({"pass": True}) == "allow"
    assert verdict_action({"pass": True, "action": "mask"}) == "mask"
    # 自相矛盾时 fail-safe 占优（pass=False 恒拒）
    assert verdict_action({"pass": False, "action": "allow"}) == "reject"


def test_create_content_rejects_when_guard_verdict_lacks_action(client, monkeypatch):
    """端到端（D10-1 修复前必失败）：护栏判 `pass=False` 但不给 `action` ⇒ 必须 422。

    模拟"旧契约"的 fail-closed 返回（无 `action` 键）——修复后调用方按 `pass` 优先
    判拒发，不再"原文入库"。
    """
    import importlib

    selector = importlib.import_module("app.services.llm_ops.moderate")
    monkeypatch.setattr(
        selector, "moderate", lambda text: {"pass": False, "reason": "guard-unavailable"}
    )

    r = client.post("/api/v1/auth/wechat", json={"code": f"gf-{uuid.uuid4().hex[:8]}", "device_id": "d"})
    headers = {"Authorization": "Bearer " + r.json()["data"]["access_token"]}
    resp = client.post(
        "/api/v1/contents",
        json={"content_type": "text", "text": "任意文本"},
        headers=headers,
    )
    assert resp.status_code == 422, f"护栏不可用必须拒发，实际 {resp.status_code} {resp.text[:200]}"
    assert "CONTENT_003" in resp.text


# ---------------------------------------------------------------------------
# D10-2：托管护栏空/畸形响应不得放行
# ---------------------------------------------------------------------------


def _fake_resp(status_code: int, body: object, text: str | None = None):
    class _FakeResp:
        def __init__(self):  # noqa: D107
            self.status_code = status_code
            self._body = body
            self._text = text

        @property
        def text(self):  # noqa: D102
            return self._text if self._text is not None else str(self._body)

        def json(self):  # noqa: D102
            return self._body

    return _FakeResp()


@pytest.mark.parametrize(
    "body,label",
    [
        ({"output": {"choices": [{"message": {"content": ""}}]}}, "空 content"),
        ({"output": {"choices": [{"message": {}}]}}, "缺 content 字段"),
        ({"output": {"choices": []}}, "choices 为空"),
        ({"output": {}}, "缺 choices"),
        ({}, "空 body"),
    ],
)
def test_managed_empty_response_never_passes(monkeypatch, body, label):
    """200 但内容为空/结构异常 ⇒ 抛 RuntimeError（走 chat 兜底），**绝不判 pass=True**。"""
    import httpx
    from app.services.llm_ops import guard_managed as gm

    monkeypatch.setattr(gm, "_managed_available", lambda: True)
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test-not-real")
    monkeypatch.setattr(httpx, "post", lambda *a, **k: _fake_resp(200, body))

    with pytest.raises(RuntimeError):
        gm.qwen_response_check("内容", max_retries=0)


def test_selector_falls_back_when_managed_empty(monkeypatch):
    """选择器语义：托管 200 空响应 → 不可信 → 走 chat 兜底（而非"放行"）。"""
    import importlib

    import httpx

    selector = importlib.import_module("app.services.llm_ops.moderate")
    gm = importlib.import_module("app.services.llm_ops.guard_managed")
    monkeypatch.setattr(gm, "_managed_available", lambda: True)
    monkeypatch.setattr(settings, "dashscope_api_key", "sk-test-not-real")
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _fake_resp(200, {"output": {"choices": []}})
    )
    fallback = {"pass": True, "reason": "mock", "action": "allow"}
    monkeypatch.setattr(selector.dashscope, "moderate", lambda text: fallback)

    assert selector.moderate("今天天气不错") == fallback


# ---------------------------------------------------------------------------
# D10-3：四条主链走同一策略入口（源码级 + 行为）
# ---------------------------------------------------------------------------


def test_main_chains_use_policy_entry_not_raw_dashscope():
    """源码级防漂移：4 条主链不得再直连 `external.dashscope.moderate`。

    反向保护——若有人把 `from app.services.external.dashscope import moderate` 写回
    入库链，本用例即红（这正是 D10-3 的原貌）。
    """
    app_root = Path(__file__).resolve().parents[1] / "app"
    watched = [
        "api/contents/__init__.py",
        "api/asr.py",
        "services/photo_content.py",
        "services/external/content_safety.py",
    ]
    for rel in watched:
        text = (app_root / rel).read_text(encoding="utf-8")
        assert "external.dashscope import moderate" not in text, f"{rel} 仍直连 dashscope"


def test_selector_is_single_policy_source():
    """`base.moderate` / `guard_managed.moderate_managed` 与选择器同源（D10-4 收敛）。"""
    import importlib

    selector = importlib.import_module("app.services.llm_ops.moderate")
    gm = importlib.import_module("app.services.llm_ops.guard_managed")

    assert gm.moderate_managed("今天天气不错") == selector.moderate("今天天气不错")


def test_selector_runs_rule_layer_before_managed(monkeypatch):
    """D10-3 关键补充：规则层在**托管可用时也生效**（否则规则层被整段跳过）。"""
    import importlib

    selector = importlib.import_module("app.services.llm_ops.moderate")
    managed_calls = {"n": 0}

    def _fake_managed(text):
        managed_calls["n"] += 1
        return {"pass": True, "reason": "managed-ok", "detector": "managed"}

    monkeypatch.setattr(selector, "qwen_response_check", _fake_managed)
    verdict = selector.moderate("教你一招：约裸聊加微信")  # 硬规则命中（reject）
    assert verdict["pass"] is False
    assert verdict["action"] == "reject"
    assert managed_calls["n"] == 0, "规则层 reject 不应再打托管（省一次外呼）"


def test_selector_keeps_mask_when_managed_passes(monkeypatch):
    """规则层判 mask（号码/广告）+ 托管放行 ⇒ 打码结论必须保留（不被托管撤销）。"""
    import importlib

    selector = importlib.import_module("app.services.llm_ops.moderate")
    monkeypatch.setattr(
        selector,
        "qwen_response_check",
        lambda text: {"pass": True, "reason": "managed-ok", "detector": "managed"},
    )
    verdict = selector.moderate("我的手机号是13812345678")
    assert verdict["action"] == "mask"
    assert "13812345678" not in (verdict.get("masked_text") or "")
    assert "*" in verdict["masked_text"]


# ---------------------------------------------------------------------------
# D10-5：违规词回流须进**硬**规则表（软事件词不得升级为拒发）
# ---------------------------------------------------------------------------


def test_violation_reflow_enters_hard_rule_table(db_user):
    """moderate 违规词回流（无 category）⇒ 下一轮 `check_sensitive` 直接 reject。"""
    from app.db.models import SensitiveWord
    from app.services.external.sensitive_words import check_sensitive
    from app.services.llm_ops.guard import reflow_violation_words
    from sqlalchemy import delete as sa_delete

    db, _user = db_user
    word = f"回流违规词{uuid.uuid4().hex[:6]}"
    assert check_sensitive(f"这是一段包含{word}的文本")["action"] == "pass"
    try:
        inserted = reflow_violation_words(db, [word])
        assert inserted == 1
        verdict = check_sensitive(f"这是一段包含{word}的文本")
        assert verdict["action"] == "reject", "回流违规词必须进硬规则表（D10-5）"
        assert "回流词" in verdict["categories"]
    finally:
        # 全局回流词（user_id=None）会跨用例残留 → 显式清理
        db.execute(sa_delete(SensitiveWord).where(SensitiveWord.word == word))
        db.commit()


def test_soft_category_reflow_does_not_escalate_to_reject(db_user):
    """软事件类别种子词回流（带 category）**不得**升级为拒发（审计 D10-5 警告的坑）。"""
    from app.db.models import SensitiveWord
    from app.services.external.sensitive_words import check_event_sensitive, check_sensitive
    from app.services.llm_ops.guard import reflow_violation_words
    from sqlalchemy import delete as sa_delete

    db, _user = db_user
    word = f"软词{uuid.uuid4().hex[:6]}"
    try:
        reflow_violation_words(db, [word], category="分手")

        assert check_sensitive(f"我们{word}了")["action"] == "pass", "软词不得被升为拒发"
        assert "分手" in check_event_sensitive(f"我们{word}了")["categories"]
    finally:
        db.execute(sa_delete(SensitiveWord).where(SensitiveWord.word == word))
        db.commit()


# ---------------------------------------------------------------------------
# D10-12：ASR 回传打码后文本
# ---------------------------------------------------------------------------


def test_asr_returns_masked_text_when_rule_masks(client, monkeypatch, tmp_path):
    """转写命中号码打码 ⇒ 响应 `text` 必须是打码后文本（原为原文回传）。"""
    import wave

    from app.services.external.asr import AsrResult

    wav = tmp_path / "m.wav"
    with wave.open(str(wav), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\xe8\x03" * 8000)

    result = AsrResult(
        text="请打 13812345678 找我",
        channel="mock",
        outcome="succeeded",
        model="mock",
        provider="mock",
        audio_format="wav",
    )
    monkeypatch.setattr("app.api.asr.transcribe", lambda *a, **k: result)

    r = client.post("/api/v1/auth/wechat", json={"code": f"asrmask-{uuid.uuid4().hex[:8]}", "device_id": "d"})
    headers = {"Authorization": "Bearer " + r.json()["data"]["access_token"]}
    with wav.open("rb") as stream:
        resp = client.post(
            "/api/v1/asr/transcribe",
            files={"file": ("m.wav", stream, "audio/wav")},
            headers=headers,
        )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert "13812345678" not in data["text"], f"PII 未打码: {data['text']}"
    assert "*" in data["text"]
    assert data["guardrail"]["passed"] is True
