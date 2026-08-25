"""事件级敏感分类器测试（B5b Wave1 · Agent C）

覆盖：
  - mark_sensitive_on_ingest：规则命中 → sensitive_tags + sensitive_status="敏感"
  - LLM 补漏（monkeypatch guard.detect_event_sensitive；mock 模式真实调用返回 []）
  - 敏感有效期：提及计数 +1、≥3 次降级普通话题（历史内容同步降级）
  - 违规词回流：LLM 判敏感且规则未覆盖 → SensitiveWord(level=3)；moderate 命中词回流
  - DB 回流词参与判定（sensitive_words 表 level 2/3：全局 + 用户级）
  - 检测器接口抽象：规则/托管/自部署三实现
前置：PG yishu 库（与 test_echo 同环境）
"""
import uuid

import pytest
from app.db.models import Content, SensitiveWord, User
from app.db.session import SessionLocal
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration


@pytest.fixture()
def db_user():
    db = SessionLocal()
    user = User(phone=f"evt-sens-{uuid.uuid4().hex[:8]}", status=1)
    db.add(user)
    db.commit()
    db.refresh(user)
    yield db, user
    db.execute(sa_delete(Content).where(Content.user_id == user.id))
    db.execute(sa_delete(SensitiveWord).where(SensitiveWord.user_id == user.id))
    db.execute(sa_delete(SensitiveWord).where(SensitiveWord.user_id.is_(None)))
    db.delete(user)
    db.commit()
    db.close()
    # 清理进程内热加入的回收词，避免跨用例污染
    from app.services.external.sensitive_words import _EVENT_REFLUX_WORDS

    _EVENT_REFLUX_WORDS.clear()


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


def test_rule_hit_writes_tags_and_status(db_user):
    db, user = db_user
    c = _content(db, user.id, "去年我们分手了，后来再也没联系")
    _mark(db, c)
    assert c.sensitive_status == "敏感"
    assert c.sensitive_tags is not None
    assert "分手" in c.sensitive_tags["categories"]
    assert c.sensitive_tags["source"] == "rule"
    assert c.sensitive_tags["mention_count"] == 1
    assert c.sensitive_tags["detected_at"]


def test_llm_fallback_when_rule_misses(db_user, monkeypatch):
    db, user = db_user
    # 规则词表不覆盖的表达 → LLM 补漏（monkeypatch 模拟 qwen-flash 判定）
    c = _content(db, user.id, "他说我们到此为止吧，那天雨很大")
    import app.services.pipeline_ext.sensitive as ext_sensitive

    monkeypatch.setattr(ext_sensitive, "detect_event_sensitive", lambda text: ["分手"])
    _mark(db, c)
    assert c.sensitive_status == "敏感"
    assert c.sensitive_tags["source"] == "llm"
    assert "分手" in c.sensitive_tags["categories"]


def test_mock_mode_llm_returns_empty(db_user):
    """mock/未配 key：LLM 补漏返回 []（静默降级，规则已兜底）——真实调用不炸"""
    db, user = db_user
    c = _content(db, user.id, "他说我们到此为止吧，那天雨很大")
    _mark(db, c)
    assert c.sensitive_status == "正常"
    assert c.sensitive_tags is None


def test_mention_count_and_downgrade(db_user):
    db, user = db_user
    a = _content(db, user.id, "去年我们分手了")
    b = _content(db, user.id, "今天又想起分手的事")
    _mark(db, a)
    _mark(db, b)
    assert a.sensitive_status == "敏感"
    assert b.sensitive_status == "敏感"
    # 第三次主动提及 → 降级普通话题：本条不标敏感，历史内容同步降级
    c = _content(db, user.id, "彻底分手了，都过去了")
    _mark(db, c)
    assert c.sensitive_status == "正常"
    assert c.sensitive_tags["downgraded"] is True
    assert c.sensitive_tags["mention_count"] == 3
    db.refresh(a)
    db.refresh(b)
    assert a.sensitive_status == "正常" and a.sensitive_tags["downgraded"] is True
    assert b.sensitive_status == "正常" and b.sensitive_tags["downgraded"] is True


def test_downgrade_is_sticky(db_user):
    """降级后同话题新内容不再标敏感（计数含已降级内容，不会反复横跳）"""
    db, user = db_user
    texts = ["分手第一次", "分手第二次", "分手第三次", "分手第四次"]
    for t in texts:
        c = _content(db, user.id, t)
        _mark(db, c)
    db.refresh(c)
    assert c.sensitive_status == "正常"
    assert c.sensitive_tags["downgraded"] is True


def test_reflow_llm_categories_writes_level3(db_user, monkeypatch):
    db, user = db_user
    c = _content(db, user.id, "他说我们到此为止吧")
    import app.services.pipeline_ext.sensitive as ext_sensitive

    monkeypatch.setattr(ext_sensitive, "detect_event_sensitive", lambda text: ["分手"])
    _mark(db, c)
    rows = db.query(SensitiveWord).filter(
        SensitiveWord.word == "分手", SensitiveWord.user_id.is_(None)
    ).all()
    assert len(rows) == 1
    assert rows[0].level == 3


def test_db_reflux_word_participates(db_user):
    """DB 回流词（level=3 全局）参与事件级判定：'绝交' 不在文件词表，入表后命中"""
    db, user = db_user
    db.add(SensitiveWord(word="绝交", level=3, user_id=None))
    db.commit()
    c = _content(db, user.id, "我们彻底绝交了")
    _mark(db, c)
    assert c.sensitive_status == "敏感"
    assert "回流词" in c.sensitive_tags["categories"]
    assert "绝交" in c.sensitive_tags["matched"]


def test_reflow_violation_words_idempotent(db_user):
    """moderate 命中词回流：写 SensitiveWord(level=3)，重复回流幂等（0 新增）"""
    from app.services.llm_ops.guard import reflow_violation_words

    db, user = db_user
    n1 = reflow_violation_words(db, ["法轮功", "裸聊"])
    assert n1 == 2
    n2 = reflow_violation_words(db, ["法轮功"])
    assert n2 == 0
    rows = db.query(SensitiveWord).filter(SensitiveWord.user_id.is_(None)).all()
    words = {r.word for r in rows}
    assert {"法轮功", "裸聊"} <= words


def test_detector_abstraction(db_user):
    """检测器接口抽象：规则/托管/自部署三实现；当前接线 = 规则 + llm_ops.guard"""
    from app.services.llm_ops.guard import (
        ManagedDetector,
        RuleDetector,
        SelfHostedDetector,
        get_detectors,
    )

    detectors = get_detectors()
    assert [d.name for d in detectors] == ["rule", "managed", "self_hosted"]
    r = RuleDetector().detect("去年我们分手了")
    assert r.pass_ is False and "分手" in r.categories
    assert RuleDetector().available() is True
    # mock 模式：托管护栏不可用（未配 key），自部署未接线
    assert ManagedDetector().available() is False
    assert SelfHostedDetector().available() is False


def test_hook_no_text_noop(db_user):
    db, user = db_user
    c = _content(db, user.id, "   ")
    c.text = ""
    db.commit()
    _mark(db, c)
    assert c.sensitive_status == "正常"
    assert c.sensitive_tags is None
