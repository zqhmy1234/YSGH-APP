"""生成态输出护栏（D10-8 · 2026-09-25 用户拍板「按建议来」）。

**缺陷原貌**：护栏只作用于**用户原文**，而用户可见的**生成文本**四条链全无防线：
照片 AI 描述（详情页）、事件名/描述（时间轴卡）、画像**开放新值**（画像页）、
Agent 回复（对话页）⇒ 用户拍一张"纸上写着违规内容"的照片，模型会把它**复述**成描述。

**拍板口径（选项 B + C）**：
- B：四条链**同步走规则层**（零成本、确定性）；
- C：`api/chat` 追加 LLM 级（直接可见、量小）；照片描述按比例**确定性抽样**走 LLM 级。

本文件逐链验证"命中即不展示/不落库"，并验证抽样与 fail-safe 口径。
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

# 硬规则（reject 类）词：规则层必然命中
_BAD_TEXT = "裸聊"


# ---------------------------------------------------------------------------
# 单元：筛查入口
# ---------------------------------------------------------------------------


def test_rule_screen_blocks_hard_rule_text():
    from app.services.llm_ops.output_guard import rule_screen

    v = rule_screen(_BAD_TEXT)
    assert v["pass"] is False
    assert v["detector"] == "rule"
    assert rule_screen("今天在公园散步，很好看")["pass"] is True


def test_screen_generated_deep_only_calls_llm_when_rule_passes(monkeypatch):
    """规则层已拦 ⇒ 不再调 LLM（省一次调用）；规则层通过 + deep ⇒ 调 LLM。"""
    from app.services.llm_ops import output_guard

    calls: list[str] = []

    def _fake_llm(text: str) -> dict:
        calls.append(text)
        return {"pass": False, "action": "reject", "reason": "managed-block", "detector": "managed"}

    monkeypatch.setattr(output_guard, "llm_screen", _fake_llm)

    assert output_guard.screen_generated(_BAD_TEXT, deep=True)["pass"] is False
    assert calls == [], "规则层已拦却仍调了 LLM（浪费成本）"

    v = output_guard.screen_generated("普通文本", deep=True)
    assert calls == ["普通文本"] and v["pass"] is False

    calls.clear()
    assert output_guard.screen_generated("普通文本", deep=False)["pass"] is True
    assert calls == [], "deep=False 不应触达 LLM"


def test_llm_screen_failsafe_on_missing_action_key(monkeypatch):
    """fail-safe：LLM 护栏判 `pass=False` 但**缺 `action` 键**也必须算拦截（D10-1 教训）。"""
    from app.services.llm_ops import base, output_guard

    monkeypatch.setattr(base, "moderate", lambda text: {"pass": False, "reason": "guard-unavailable"})
    assert output_guard.llm_screen("x")["pass"] is False


def test_sampled_is_deterministic_and_bounded():
    from app.services.llm_ops.output_guard import sampled

    sample_key = "content-abc"  # noqa: S105 —— 抽样键，非凭据（ruff S105 误判命名）
    assert sampled(sample_key, 0.5) == sampled(sample_key, 0.5)
    assert sampled(sample_key, 0.0) is False
    assert sampled(sample_key, 1.0) is True
    hit = sum(1 for i in range(2000) if sampled(f"t{i}", 0.1))
    assert 120 <= hit <= 280, f"10% 抽样实际命中 {hit}/2000，比例明显偏离"


# ---------------------------------------------------------------------------
# 链①：照片 AI 描述 —— 命中则不落库（抛错，调用方按"描述跳过"处理）
# ---------------------------------------------------------------------------


def test_photo_description_blocked_not_stored(monkeypatch):
    from app.services import ai_tagging

    monkeypatch.setattr(ai_tagging, "llm_available", lambda: True)
    monkeypatch.setattr(ai_tagging, "_resolve_image", lambda content, path: ("dummy.jpg", None))
    monkeypatch.setattr(ai_tagging, "_vision", lambda path, prompt: f"照片里写着一张纸：{_BAD_TEXT}")

    class _C:
        id = "c-1"
        extra: dict = {}

    with pytest.raises(ai_tagging.AiTaggingError) as exc:
        ai_tagging.generate_photo_description(_C())
    assert exc.value.code == "OUTPUT_BLOCKED"


def test_photo_description_passes_and_is_persisted(monkeypatch):
    """放行路径回归：正常描述照常返回（且抽样未命中时不会额外调 LLM）。"""
    from app.services import ai_tagging
    from app.services.llm_ops import output_guard

    monkeypatch.setattr(ai_tagging, "llm_available", lambda: True)
    monkeypatch.setattr(ai_tagging, "_resolve_image", lambda content, path: ("dummy.jpg", None))
    monkeypatch.setattr(ai_tagging, "_vision", lambda path, prompt: "夕阳下的海边")
    monkeypatch.setattr(output_guard, "llm_screen", lambda text: pytest.fail("deep 抽样命中才该调 LLM"))

    class _C:
        id = "c-pass"
        extra: dict = {}

    assert ai_tagging.generate_photo_description(_C()) == "夕阳下的海边"


# ---------------------------------------------------------------------------
# 链②：事件标题 —— 命中则退回确定性标题（不阻断聚合）
# ---------------------------------------------------------------------------


def test_event_merge_title_falls_back_on_block(monkeypatch):
    from app.services.llm_ops import event_merge

    monkeypatch.setattr(event_merge, "llm_available", lambda: True)
    monkeypatch.setattr(
        event_merge, "chat_text", lambda system, user: '{"verdict":"merge","confidence":0.9,"title":"裸聊 合集"}'
    )
    out = event_merge.merge_verdict({"tag": "聚会", "cluster": ["a", "b"]})
    assert out["title"] == "聚会 · 2 条", f"应退回确定性标题，实际 {out['title']}"
    assert out["verdict"] == "merge"  # 不阻断归并本身


# ---------------------------------------------------------------------------
# 链③：画像开放新值 —— 命中则只丢该条
# ---------------------------------------------------------------------------


def test_annotate_drops_blocked_open_value():
    from app.services.llm_ops.annotate import _normalize_hits
    from app.services.profile_schema import DimensionSpec

    spec = DimensionSpec(
        id="d1",
        label="性格",
        category="cat",
        values=("开朗",),
        phrase="{value}",
        confidence_threshold=0.7,
        disclosure="L1",
        open_enum=True,
        multi_value=False,
    )
    hits = [
        {"dimension": "d1", "enum_value": "温柔", "confidence": 0.9},
        {"dimension": "d1", "enum_value": _BAD_TEXT, "confidence": 0.9},
    ]
    out = _normalize_hits(hits, [spec])
    assert [h["enum_value"] for h in out] == ["温柔"], "只该丢掉命中护栏的那一条"


# ---------------------------------------------------------------------------
# 链④：对话回复 —— 命中则不落库、不返回文本（端到端）
# ---------------------------------------------------------------------------


class _FakeResult:
    text = f"我建议你试试{_BAD_TEXT}"
    latency_ms = 12


def _patch_chat(monkeypatch, *, reply_text: str, guard_pass: bool):
    import app.api.chat as chat
    from app.services.external.agent import AgentChatResult  # noqa: F401  仅类型存在性

    def _call(user_id, message, thread_id):  # noqa: ANN001
        r = _FakeResult()
        r.text = reply_text
        return r

    monkeypatch.setattr(chat, "call_agent_chat", _call)
    monkeypatch.setattr(
        chat,
        "screen_generated",
        lambda text, deep=False: {"pass": guard_pass, "action": "allow" if guard_pass else "reject",
                                  "reason": "", "detector": "rule", "matched": [], "categories": []},
    )


def test_chat_reply_blocked_not_persisted(client, auth_headers, monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_BASE_URL", "http://127.0.0.1:8300")
    user_id, headers = auth_headers("chatguard")
    _patch_chat(monkeypatch, reply_text="被拦的回复", guard_pass=False)

    r = client.post("/api/v1/chat/messages", json={"content": "你好"}, headers=headers)
    assert r.status_code == 422, r.text
    assert r.json()["code"] == "CHAT_004"

    from app.db.models import ChatMessage
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.query(ChatMessage).filter(ChatMessage.user_id == user_id).all()
        assert [x.role for x in rows] == ["user"], "被拦回复不得落库（用户消息应留痕以便复盘）"
    finally:
        db.close()


def test_chat_reply_allowed_persisted(client, auth_headers, monkeypatch):
    user_id, headers = auth_headers("chatok")
    _patch_chat(monkeypatch, reply_text="当然可以，慢慢说", guard_pass=True)

    r = client.post("/api/v1/chat/messages", json={"content": "你好"}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["data"]["reply"]["text"] == "当然可以，慢慢说"

    from app.db.models import ChatMessage
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        rows = db.query(ChatMessage).filter(ChatMessage.user_id == user_id).all()
        assert sorted(x.role for x in rows) == ["assistant", "user"]
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 按链覆盖清单（防"漏接一条链"回归）
# ---------------------------------------------------------------------------


def test_all_four_chains_are_wired():
    """源码级防漂移：四条链必须都引用生成态护栏（删掉任一处即红）。"""
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "app"
    watched = {
        "services/ai_tagging.py": "screen_generated",
        "services/llm_ops/event_merge.py": "screen_generated",
        "services/llm_ops/annotate.py": "screen_generated",
        "api/chat.py": "screen_generated",
    }
    for rel, needle in watched.items():
        text = (root / rel).read_text(encoding="utf-8")
        assert needle in text, f"{rel} 未接生成态护栏（{needle} 缺失）"
    # 事件名/描述链只能在**用户可见**处拦（judge_merge 返回 title），故注释里须写明
    assert "确定性标题" in (root / "services/llm_ops/event_merge.py").read_text(encoding="utf-8")
