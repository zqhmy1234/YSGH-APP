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
