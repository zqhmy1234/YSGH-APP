"""B-07 回归：用户纠错不被后续分类覆写（2026-10-24 · 待办总账 B-07）

## 缺陷

用户手动改正分类后，后续流水线（RQ 重投 / `requeue_job` 超龄重扫 / `_process_voice`
二次分类 / 重传）会把这个 `content_class` 又写回模型输出，并把 `class_source` 标成
`setfit` ⇒ **用户在用的功能被静默吃掉**。项目硬约束：AGENTS.md「用户操作优先：
手动合并/拆分/确认后，自动算法永不覆盖」。

## 取证结论（本文件的断言建立在实测之上，不是注释推断）

- `POST /api/v1/corrections`（`api/corrections.py:67-69`）source=active 时**只写**
  `content.content_class`，**从不写 `class_source`**；
- `services/correction.py::record_correction` 只写 `correction_log` + Qdrant，不碰 contents；
- 全仓 `class_source` 的**赋值点只有** `services/pipeline.py`（`= "setfit"`）。

⇒ 「纠错后 `class_source == "correction"`」在存量数据里**从不成立**（历史行是 None 或
`setfit`）。若守卫只按该判据写，**永不生效**。故真值来源 = `correction_log` 里该 content
的最新 **active** 行；`class_source` 标记只作快速路径。

## 覆盖

1. 纠错后重跑 `_classify_content` → `content_class` 不变（模型跑不跑都不得覆盖）
2. 未纠错的正常覆写（守卫不能把正常分类也挡住 —— 防止"修成恒不分类"）
3. 被动信号（echo / org）**不构成**保护依据（它们本就不写权威字段）
4. 历史数据兼容：`class_source` 尚未被纠错侧写过（None）时同样受保护 ← 本缺陷的主形态
5. **负向探针**：把守卫摘掉（令判据恒返回 None）时 ① 必须变红 —— 证明测试有鉴别力

前置：PG yishu 库（同 test_pipeline / test_correction）。**不需要** Qdrant / BGE-M3：
纠错行直接落库（`record_correction` 的向量化不是本缺陷的判据）。
"""
import uuid

import pytest
from app.db.models import Content, CorrectionLog
from sqlalchemy import select

pytestmark = pytest.mark.integration

_MODEL_LABEL = "todo"      # 模型会给的标签
_USER_LABEL = "idea"       # 用户纠正成的标签


def _make_content(db, user_id: str, **kw) -> Content:
    """建一条文本内容（默认 status=done、无纠错）"""
    c = Content(
        id=str(uuid.uuid4()),
        user_id=user_id,
        content_type="text",
        text=kw.pop("text", "今天想去吃火锅"),
        status="done",
        source="app",
        **kw,
    )
    db.add(c)
    db.commit()
    db.refresh(c)
    return c


def _add_correction(db, user_id: str, content: Content, new_label: str, source: str = "active") -> CorrectionLog:
    """直接落一条 correction_log 行（跳过向量化——Qdrant 不是本缺陷判据）"""
    row = CorrectionLog(
        user_id=user_id,
        content_id=str(content.id),
        content_type=content.content_type,
        new_label=new_label,
        old_label="mixed",
        source=source,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@pytest.fixture()
def stub_classifier(monkeypatch):
    """把 SetFit 换成定值桩（不加载大模型；返回值可断言"模型确实给了别的标签"）"""
    calls: list[str] = []

    def _fake(text):
        calls.append(text)
        return {"label": _MODEL_LABEL, "label_cn": "待办", "confidence": 0.95}

    monkeypatch.setattr("app.services.pipeline._get_classifier", lambda: _fake)
    return calls


class TestCorrectionProtection:
    def test_corrected_content_not_overwritten(self, db_user, stub_classifier):
        """① 纠错后重跑分类 → content_class 保持用户值，class_source 不被改成 setfit

        这是缺陷的本体：用户改成 idea，模型说 todo ⇒ 必须是 idea。
        """
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL)
        # 模拟 API 层的回写（active 纠错只写 content_class，**不写 class_source**）
        c.content_class = _USER_LABEL
        db.commit()

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        assert c.content_class == _USER_LABEL, "用户纠错被模型覆写（B-07 复发）"
        assert c.class_source != "setfit", "纠错后的内容不应被标注为 setfit 来源"

    def test_uncorrected_content_is_classified_normally(self, db_user, stub_classifier):
        """② 未纠错的内容照常覆写（守卫不得把正常分类也挡掉）"""
        db, user = db_user
        c = _make_content(db, user.id)

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        assert c.content_class == _MODEL_LABEL
        assert c.class_source == "setfit"
        assert c.model_version == "setfit-v1"
        assert stub_classifier, "未纠错的内容应当真的跑了模型"

    def test_skip_is_observable(self, db_user, stub_classifier):
        """①附：跳过要在 extra 留痕（否则"分类没跑"与"跑了但没变"事后不可区分）

        口径同台账 §7.20「降级要可观测，不静默」。
        """
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL)
        c.content_class = _USER_LABEL
        db.commit()

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        mark = (c.extra or {}).get("classification_skipped")
        assert mark is not None, "跳过分类必须留痕（可观测性）"
        assert mark["reason"] == "user-correction"
        assert "correction_log" in mark["evidence"], f"应记下判据来源，实际 {mark}"
        assert stub_classifier == [], "已纠错内容不应再跑模型（省 27s 推理）"

    def test_legacy_row_without_class_source_marker_is_protected(self, db_user, stub_classifier):
        """④ 历史兼容（本缺陷主形态）：纠错侧从不写 class_source ⇒ 该列仍是 None

        这条钉住「不能只靠 class_source 判据」：若守卫写成
        `if content.class_source == "correction": return`，本用例必红（守卫不生效）。
        """
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL)
        c.content_class = _USER_LABEL
        assert c.class_source is None, "取证前提：纠错路径不写 class_source"
        db.commit()

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        assert c.content_class == _USER_LABEL, "class_source 为 None 的已纠错内容必须同样受保护"

    def test_passive_correction_does_not_protect(self, db_user, stub_classifier):
        """③ echo / org 是被动信号，不写权威字段 ⇒ 也不构成保护依据

        依据：`api/corrections.py:64-66`「仅 active 来源回写；echo/org 属被动信号，
        须过服务层三道闸门后才影响层①，不动权威字段」。若把 echo 也算保护，
        一次随手误点的回声会永久冻结这条内容的分类。
        """
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL, source="echo")

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        assert c.content_class == _MODEL_LABEL, "被动纠错不应冻结分类"
        assert c.class_source == "setfit"

    def test_latest_active_correction_wins_over_echo(self, db_user, stub_classifier):
        """Lead 要求：latest active 口径写死——同时存在 active + 更晚的 echo 时，

        保护依据仍是 **active** 行（echo 不参与，哪怕它更晚）。
        """
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL, source="active")
        _add_correction(db, user.id, c, "quote", source="echo")  # 更晚但被动
        c.content_class = _USER_LABEL
        db.commit()

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        assert c.content_class == _USER_LABEL
        assert stub_classifier == []

    def test_user_then_active_re_correction_keeps_latest(self, db_user, stub_classifier):
        """同内容多次 active 纠错以最后一次为准（latest 口径）"""
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, "idea", source="active")
        _add_correction(db, user.id, c, "quote", source="active")
        c.content_class = "quote"
        db.commit()

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        assert c.content_class == "quote", "应保护最后一次 active 纠错"


class TestNegativeProbe:
    """负向探针：证明上面的断言**有鉴别力**（本仓铁律，见
    `skills/harness-gate-maintenance/SKILL.md`「恒绿无鉴别力」）。

    做法（等价于"把守卫注掉"）：令判据函数恒返回 None —— 这正是「守卫不存在」时
    `_classify_content` 看到的世界。若此时 `content_class` **仍不被覆写**，说明
    ① 类断言是靠别的东西（缓存/未刷新/根本没跑）变绿的，测试不可信。
    """

    def test_guard_removal_makes_overwrite_happen(self, db_user, stub_classifier, monkeypatch):
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL)
        c.content_class = _USER_LABEL
        db.commit()

        # 摘掉守卫：判据恒判"无纠错"（＝守卫被删掉时 _classify_content 的行为）
        monkeypatch.setattr(
            "app.services.pipeline._user_decided_class", lambda db_, content_: None
        )

        from app.services.pipeline import _classify_content

        _classify_content(db, c, c.text)
        db.commit()
        db.refresh(c)

        # 守卫不在 ⇒ 必然覆写。这正是 ①/④ 断言要抓的形态。
        assert c.content_class == _MODEL_LABEL, (
            "负向探针失效：即便摘掉守卫也没有覆写，说明 ①/④ 的断言不是靠守卫变绿的"
        )
        assert c.class_source == "setfit"

    def test_guard_reads_correction_log_not_just_marker(self, db_user, stub_classifier):
        """判据必须真的查 correction_log（而不是只看 class_source）

        反过来钉一次：**有 active 纠错行**即保护，哪怕 class_source 被写成 setfit
        （历史脏数据的形态——本次修复前被覆写过的行就是这样）。
        """
        db, user = db_user
        c = _make_content(db, user.id)
        _add_correction(db, user.id, c, _USER_LABEL)
        c.content_class = _USER_LABEL
        c.class_source = "setfit"   # 历史已被覆写过的形态
        db.commit()

        from app.services.pipeline import _user_decided_class

        assert _user_decided_class(db, c) is not None, (
            "class_source=setfit 但存在 active 纠错行 ⇒ 仍须判为已纠错（否则历史脏数据会二次被吃）"
        )


class TestEndToEndViaApi:
    """走真实 API 落纠错（证明取证结论 + 保护链闭环）"""

    def test_api_correction_then_classify_keeps_user_label(self, db_user, stub_classifier):
        from app.api import deps
        from app.main import app
        from fastapi.testclient import TestClient

        db, user = db_user
        c = _make_content(db, user.id, text="会议室改成方案评审")
        client = TestClient(app)
        app.dependency_overrides[deps.get_current_user] = lambda: user
        try:
            r = client.post(
                "/api/v1/corrections",
                json={
                    "content_id": str(c.id),
                    "text": "会议室改成方案评审",
                    "new_label": _USER_LABEL,
                    "old_label": "mixed",
                    "source": "active",
                },
            )
            assert r.status_code == 200, r.text
            db.expire_all()
            row = db.scalar(select(Content).where(Content.id == c.id))
            assert row.content_class == _USER_LABEL
            # 取证前提留存：API 层**不写** class_source（故守卫不能只靠它）
            assert row.class_source is None

            from app.services.pipeline import _classify_content

            _classify_content(db, row, row.text)
            db.commit()
            db.expire_all()
            row = db.scalar(select(Content).where(Content.id == c.id))
            assert row.content_class == _USER_LABEL, "走 API 纠错后仍被分类覆写（B-07 复发）"
        finally:
            app.dependency_overrides.clear()
