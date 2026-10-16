"""批次 8 审计（2026-10-14）抓到的「静默失败」回归守护：台账 §7.19 / §7.20。

为什么单独成文件：这些用例是**纯单元**（不碰库、不碰网络，只依赖 monkeypatch 与日志捕获），
而相邻的 `test_wechat_media.py` 整文件标了 `pytest.mark.integration`（需 PG）。

覆盖：
  - §7.19（A1）：企微媒体在**生产环境**缺凭证时必须**抛错**，绝不返回 mock 字节——
    原实现返回 64×64 纯紫 JPEG / 0.1s 静音 WAV，调用方会把它们当**真实记忆**落库。
  - §7.20（A2）：主提交后无可索引文本时**必须留痕**（原为静默 return ⇒ "status=done
    但永远搜不到"这条事实在日志里毫无痕迹）。
"""
import logging
import uuid

import pytest
from app.core.config import settings
from app.db.models import Content
from app.services.pipeline import _index_after_commit
from app.services.wechat import service as wx


def test_wechat_media_refuses_mock_in_production(monkeypatch):
    """§7.19：生产环境 + 无 token ⇒ 抛 `WECHAT_MEDIA_UNAVAILABLE`，不产出假媒体。

    把 `_corp_access_token` 打桩成 None（而非改配置字段）——精确命中"凭证不可用"这一
    前置条件，且不依赖 secret 的字段名/回退逻辑（那部分是另一条链路，有自己的测试）。
    """
    monkeypatch.setattr(settings, "app_env", "production")
    monkeypatch.setattr(wx, "_corp_access_token", lambda: None)

    with pytest.raises(RuntimeError) as ei:
        wx.download_media("MEDIA_X", "image")
    assert "WECHAT_MEDIA_UNAVAILABLE" in str(ei.value)


def test_wechat_media_mock_still_allowed_outside_production(monkeypatch):
    """§7.19 的另一半：**冻结的是"生产不造假"，不是砍掉 mock 能力**。

    联调/沙箱（非 production）必须保持原有的 mock 回退——否则本地与 CI 的媒体链路全断。
    """
    monkeypatch.setattr(settings, "app_env", "development")
    monkeypatch.setattr(wx, "_corp_access_token", lambda: None)

    assert len(wx.download_media("MEDIA_X", "image")) > 0
    assert len(wx.download_media("MEDIA_X", "voice")) > 0


def test_index_after_commit_logs_when_text_is_empty(caplog):
    """§7.20：无可索引文本时**留痕**（否则"done 但搜不到"无从判断）。

    直接传 `db=None`：被测路径在触碰 db 之前就应返回——这同时钉住了"它是纯跳过、
    不是半途写库"这一事实。
    """
    content = Content(id=uuid.uuid4(), content_type="photo", text=None)

    with caplog.at_level(logging.INFO):
        _index_after_commit(None, content)

    assert any("索引跳过" in r.getMessage() for r in caplog.records), caplog.text


# ── §7.22（批次 8 续审）：**生产环境不得用规则猜测冒充 LLM 输出** ──────────────
# 两处 mock 通道（画像标注 / L2 归并）原本都没有生产守卫，而它们的产物会**入库并展示**：
# 前者写进 `user_profile.dimensions` 并在画像页展示；后者按 `confidence >= 0.7` 把事件**转正**。


class _StubSpec:
    """`annotate` 只用到 spec 的四个属性（duck typing），故无需构造真实维度对象。"""

    def __init__(self) -> None:
        self.id = "test_dim"
        self.values = ["喝茶"]
        self.aliases: dict[str, str] = {}
        self.multi_value = False


def _stub_annotate_env(monkeypatch, ann, app_env: str) -> None:
    """把 annotate 的外部依赖全部打桩：LLM 不可用 + 标注池用桩 + 环境指定。"""
    monkeypatch.setattr(ann, "llm_available", lambda: False)
    monkeypatch.setattr(ann, "get_schema", lambda: None)
    monkeypatch.setattr(ann, "_pool_for", lambda schema, hint: [_StubSpec()])
    monkeypatch.setattr(settings, "app_env", app_env)


def test_annotate_refuses_mock_in_production(monkeypatch):
    """§7.22：生产 + LLM 不可用 ⇒ **返回空**（不产出画像命中），绝不返回规则猜测。"""
    from app.services.llm_ops import annotate as ann

    _stub_annotate_env(monkeypatch, ann, "production")

    assert ann.annotate("今天和妈妈喝茶") == []


def test_annotate_mock_still_available_outside_production(monkeypatch):
    """§7.22 的另一半：非生产保持原 mock 通道（冷启动与测试依赖它）。"""
    from app.services.llm_ops import annotate as ann

    _stub_annotate_env(monkeypatch, ann, "development")

    hits = ann.annotate("今天和妈妈喝茶")
    assert len(hits) == 1
    assert hits[0]["enum_value"] == "喝茶"


def test_event_merge_mock_not_promoted_in_production(monkeypatch):
    """§7.22：生产 + LLM 不可用 ⇒ confidence 压到 0（**不转正**，候选只进「待确认」）。

    调用方按 `confidence >= 0.7` 转正 ⇒ 断言必须低于该门槛，否则这条用例形同虚设。
    """
    from app.services.llm_ops import event_merge as em

    monkeypatch.setattr(em, "llm_available", lambda: False)
    monkeypatch.setattr(settings, "app_env", "production")

    out = em.merge_verdict({"tag": "旅行", "tag_hint": [], "time_range": [], "place_hint": ""})
    assert out["confidence"] < 0.7


def test_event_merge_mock_keeps_legacy_confidence_outside_production(monkeypatch):
    """§7.22：非生产保持原值（有标签 0.8）——冻结的是"生产不造假"，不是改掉 mock 语义。"""
    from app.services.llm_ops import event_merge as em

    monkeypatch.setattr(em, "llm_available", lambda: False)
    monkeypatch.setattr(settings, "app_env", "development")

    out = em.merge_verdict({"tag": "旅行", "tag_hint": [], "time_range": [], "place_hint": ""})
    assert out["confidence"] == 0.8
