"""软敏感「检测器不可用」的处置（D10-9 · 2026-09-25 用户拍板「按建议来」）。

**缺陷原貌**：`llm_ops/guard.py` 里检测器不可用/调用异常一律 `pass_=True`（静默放行），
`detect_event_sensitive` 失败返回 `[]` —— 「**检出为空**」与「**没能检**」被混为一谈。
后果：LLM 故障期，"分手/离世"这类**软话题**内容被判为"正常"，而 `echo` 的双查
（第二查走硬规则，不认软话题）也拦不住 ⇒ 回响/关怀追问可能**主动提及**用户最痛的回忆。

**拍板口径（选项 C + D）**：
- C：检测器**没跑通** ⇒ 内容标 `sensitive_status="待复核"`（**不主动提及**），
  但**入库、被动检索、用户自己翻看完全照常**；
- D：软话题词表**扩容**（规则层兜底，零成本确定性；只影响"不主动提及"）。

**刻意不采用选项 B（全链 fail-closed）**：会把"LLM 未配置"的本地开发/测试环境
整库内容标成待复核（数据污染 + 全量测试漂移），故 mock 模式视为可用。
"""
from __future__ import annotations

import uuid

import pytest
from app.db.models import Content, User
from app.db.session import SessionLocal

pytestmark = pytest.mark.integration

# 刻意**不含任何规则词**的句子（用于验证"规则未命中"分支真正走到 LLM 判定）
_NO_RULE_TEXT = "今天下午在楼下坐了很久，想了很多以前的事"


@pytest.fixture()
def db_user(cleanup_user):
    db = SessionLocal()
    user = User(phone=f"softsens-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    cleanup_user(db, user.id)
    db.delete(user)
    db.commit()
    db.close()


def _content(db, user_id: str, text: str) -> Content:
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="text",
        text=text,
        sensitive_status="正常",
        status="done",
    )
    db.add(c)
    db.commit()
    return c


def _mark(db, content: Content) -> None:
    from app.services.pipeline_ext.sensitive import mark_sensitive_on_ingest

    mark_sensitive_on_ingest(db, content)
    db.commit()


# ---------------------------------------------------------------------------
# D：规则层兜底词表扩容（口语表达）
# ---------------------------------------------------------------------------


def test_rule_layer_covers_colloquial_soft_words():
    """新增口语词必须真的被规则层命中（否则"扩容"只是注释）。"""
    from app.services.external.sensitive_words import check_event_sensitive

    cases = {
        "他跟我说感情破裂了": "分手",
        "爷爷已经不在了": "离世",
        "下周还要去化疗": "健康",
        "公司要裁员": "金钱",
        "他们俩最近分居了": "家庭矛盾",
        "收到了律师函": "法律纠纷",
        "骑车摔伤住院了": "意外创伤",
    }
    for text, expect_cat in cases.items():
        r = check_event_sensitive(text)
        assert expect_cat in r["categories"], f"{text!r} 未命中断言类别 {expect_cat}: {r}"


def test_no_rule_text_really_hits_nothing():
    """防空转：本文件用的"无规则词"句子必须真的零命中（否则后面的分支测不到）。"""
    from app.services.external.sensitive_words import check_event_sensitive

    assert check_event_sensitive(_NO_RULE_TEXT)["categories"] == []


# ---------------------------------------------------------------------------
# C：检测器没跑通 ⇒ 待复核（不主动提及），入库与被动检索照常
# ---------------------------------------------------------------------------


def test_detector_unavailable_marks_pending_recheck(db_user, monkeypatch):
    """真实模式 + 未配 key（检测器不可用）⇒ 待复核 + source=degraded。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mock_external_ai", False)
    db, user = db_user
    c = _content(db, user.id, _NO_RULE_TEXT)
    _mark(db, c)

    assert c.sensitive_status == "待复核", f"应标待复核，实际 {c.sensitive_status}"
    assert c.sensitive_tags["source"] == "degraded"
    assert c.sensitive_tags["categories"] == []


def test_pending_recheck_blocks_active_mention(db_user, monkeypatch):
    """待复核内容必须被**主动提及**路径拦住（回响双查 / 推送过滤）。"""
    from app.core.config import settings
    from app.services.echo import _is_sensitive

    monkeypatch.setattr(settings, "mock_external_ai", False)
    db, user = db_user
    c = _content(db, user.id, _NO_RULE_TEXT)
    _mark(db, c)

    assert _is_sensitive(db, c) is True, "待复核内容仍被回响取用（主动提及未拦住）"
    # 推送过滤口径：notify 只取 sensitive_status IS NULL 或 = '正常' ⇒ 待复核被排除
    assert c.sensitive_status == "待复核"


def test_llm_error_also_marks_pending_recheck(db_user, monkeypatch):
    """检测器"可用但调用失败"同样是"没能检" ⇒ 待复核（不是静默放行）。"""
    from app.core.config import settings
    from app.services.llm_ops import guard

    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(guard, "llm_available", lambda: True)

    def _boom(system, text):  # noqa: ANN001
        raise RuntimeError("LLM down")

    monkeypatch.setattr(guard, "chat_text", _boom)
    db, user = db_user
    c = _content(db, user.id, _NO_RULE_TEXT)
    _mark(db, c)
    assert c.sensitive_status == "待复核"


def test_normal_ingest_and_passive_search_unaffected(db_user, monkeypatch):
    """入库/被动检索不受影响：内容正常落库、status/管线字段不变。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mock_external_ai", False)
    db, user = db_user
    c = _content(db, user.id, _NO_RULE_TEXT)
    _mark(db, c)
    assert c.status == "done"          # 入库状态未被改动
    assert c.text == _NO_RULE_TEXT     # 原文未被改动/打码
    assert c.deleted_at is None


# ---------------------------------------------------------------------------
# 反向保护：mock 模式不被误标（否则开发/测试数据全变待复核）
# ---------------------------------------------------------------------------


def test_mock_mode_keeps_normal(db_user, monkeypatch):
    """mock 模式（本地开发/测试）视为可用 ⇒ 保持"正常"（防污染，选项 B 的副作用）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "mock_external_ai", True)
    db, user = db_user
    c = _content(db, user.id, _NO_RULE_TEXT)
    _mark(db, c)
    assert c.sensitive_status == "正常", f"mock 模式不应标待复核，实际 {c.sensitive_status}"


def test_llm_hit_still_marks_sensitive(db_user, monkeypatch):
    """检测器可用且真判出类别 ⇒ 仍走原语义（"敏感" + source=llm），未被新分支覆盖。"""
    from app.core.config import settings
    from app.services.llm_ops import guard

    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(guard, "llm_available", lambda: True)
    monkeypatch.setattr(
        guard,
        "chat_text",
        lambda system, text: "离世\n",  # noqa: ARG005
    )
    db, user = db_user
    c = _content(db, user.id, _NO_RULE_TEXT)
    _mark(db, c)
    assert c.sensitive_status == "敏感"
    assert c.sensitive_tags["source"] == "llm"
    assert "离世" in c.sensitive_tags["categories"]


def test_rule_hit_path_unchanged(db_user):
    """规则命中路径零改动（回归保护）。"""
    db, user = db_user
    c = _content(db, user.id, "去年我们分手了，后来再也没联系")
    _mark(db, c)
    assert c.sensitive_status == "敏感"
    assert "分手" in c.sensitive_tags["categories"]
