"""图片审核钩子的回归测试（D-24 · 2026-10-25）。

## 为什么必须测这几条

这个钩子最容易出的错**不是**"没审"，而是**"看起来审了但其实没审"**：
  · `fs` 后端没有 COS 对象 ⇒ 必须返回 `skipped` 且**明确日志**，不能返回 `allow` 假装过了；
  · 审核服务抛异常 ⇒ 必须 `error` 且**照片仍入库**（不因外部抖动卡住用户私有记忆）；
  · 命中 ⇒ 必须标 `待复核`（不是"正常"），且**不删照片**；
  · 该函数**永不抛异常**（pipeline 里任何抛出都会让整张照片 failed）。

每条用例对应一个「会骗人的形态」。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from app.services.pipeline_ext import media_safety


def _content(**over):
    base = {"id": "c-probe", "cos_key": "photos/x.jpg", "sensitive_status": "正常", "extra": {}}
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def cfg(monkeypatch):
    """固定配置：开启 + cos 后端（否则钩子会 skipped，测不到主路径）。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "content_image_moderation_enabled", True)
    monkeypatch.setattr(settings, "content_safety_provider", "tencent_ci")
    monkeypatch.setattr(settings, "storage_backend", "cos")
    return settings


def test_no_cos_key_is_skipped_not_allow(cfg):
    """没有 cos_key ⇒ `skipped`（不是 `allow`）。这是最容易被写成假绿的形态。"""
    c = _content(cos_key=None)
    assert media_safety.moderate_photo_on_ingest(None, c, None) == "skipped"
    # 关键：不能把 sensitive_status 说成"正常"就当审过了——这里不该改它，但结论必须是 skipped
    assert c.sensitive_status == "正常"


def test_fs_backend_with_cos_key_still_attempts(cfg, monkeypatch):
    """有 cos_key 时会真的调适配器（fs 后端也照调——由适配器决定成败）。"""
    from app.services.external import content_safety

    called: list[str] = []

    class Fake:
        name = "fake"

        def check_image(self, key):  # noqa: ANN001
            called.append(key)
            return {"pass": True, "labels": [], "provider": "fake"}

    monkeypatch.setattr(content_safety, "get_content_safety", lambda *a, **k: Fake())
    c = _content()
    assert media_safety.moderate_photo_on_ingest(None, c, c.cos_key) == "allow"
    assert called == ["photos/x.jpg"]


def test_hit_marks_review_and_keeps_photo(cfg, monkeypatch):
    """命中 ⇒ 标「待复核」、**不删照片**、留标签痕迹。"""
    from app.services.external import content_safety

    class Fake:
        name = "fake"

        def check_image(self, key):  # noqa: ANN001
            return {"pass": False, "labels": ["porn"], "provider": "fake", "reason": "hit"}

    monkeypatch.setattr(content_safety, "get_content_safety", lambda *a, **k: Fake())
    c = _content()
    assert media_safety.moderate_photo_on_ingest(None, c, c.cos_key) == "review"
    assert c.sensitive_status == "待复核", "命中必须标待复核（不是保持'正常'）"
    # 照片对象仍在（没有被删/没有被标 failed）——由调用方保证，这里只验字段未被破坏
    assert c.cos_key == "photos/x.jpg"


def test_adapter_exception_returns_error_and_never_raises(cfg, monkeypatch):
    """适配器抛异常 ⇒ `error`，**且本函数绝不向上抛**（抛了整张照片就 failed 了）。"""
    from app.services.external import content_safety

    def boom(*a, **k):  # noqa: ANN002, ANN003
        raise RuntimeError("provider down")

    monkeypatch.setattr(content_safety, "get_content_safety", boom)
    c = _content()
    assert media_safety.moderate_photo_on_ingest(None, c, c.cos_key) == "error"
    assert c.sensitive_status == "正常", "审核失败不得把状态改成待复核（那是误伤）"


def test_disabled_switch_is_skipped(cfg, monkeypatch):
    """开关关闭 ⇒ `skipped`（且日志会写明未审）。"""
    monkeypatch.setattr(cfg, "content_image_moderation_enabled", False)
    c = _content()
    assert media_safety.moderate_photo_on_ingest(None, c, c.cos_key) == "skipped"


def test_trace_written_to_extra(cfg, monkeypatch):
    """结论要写进 `extra.image_moderation`（事后补审的唯一筛选依据）。"""
    from app.services.external import content_safety

    class Fake:
        name = "fake"

        def check_image(self, key):  # noqa: ANN001
            return {"pass": True, "labels": [], "provider": "fake"}

    monkeypatch.setattr(content_safety, "get_content_safety", lambda *a, **k: Fake())
    c = _content()
    media_safety.moderate_photo_on_ingest(None, c, c.cos_key)
    assert c.extra.get("image_moderation", {}).get("verdict") == "allow"


# ---------------------------------------------------------------------------
# 接线测试：**钩子必须真的挂在 pipeline_photo 里**
#
# 为什么单测不够：上面 6 条全在测 `media_safety` 这个模块**自己**，
# 它们全绿**不能证明**这个模块被 `pipeline_photo` 调用过。
# 如果钩子被摘掉（或从来没接上），模块自测照样全绿 ⇒ 就是「恒绿无鉴别力」。
#
# 这条用例直接读源码断言接线存在（本仓对"不变量＝没写某行代码"的先例：
# `test_search_answer_async.py` 的 `test_async_path_skips_latency_recompute`）。
# 用源码断言而不是跑整条 picture pipeline：后者要 COS + VL + OCR，成本高且不稳定。
# ---------------------------------------------------------------------------


def test_hook_is_wired_into_photo_pipeline():
    """`_process_photo` 必须在 caption **之前**调用图片审核钩子。"""
    import inspect

    from app.services import pipeline_photo

    src = inspect.getsource(pipeline_photo._process_photo)
    assert "moderate_photo_on_ingest" in src, (
        "pipeline_photo 没有调用图片审核钩子 ⇒ 用户的照片仍然一张都没过图片审核"
    )
    # 顺序：审核必须在 caption 之前（省 VL 成本 + 防污染检索）
    idx_hook = src.index("moderate_photo_on_ingest")
    idx_caption = src.index("image_caption(")
    assert idx_hook < idx_caption, (
        "图片审核必须在 caption 之前 —— 否则违规图会先产生 caption 并被索引进 Qdrant"
    )

