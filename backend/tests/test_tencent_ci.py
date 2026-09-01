"""腾讯云 CI 图片能力测试（WP-E）

mock 单测：monkeypatch CosS3Client 的 ci_image_detect_label / ci_auditing_image_batch，
验证响应解析与鉴权配置要求。真实调用（真图冒烟）需用户同意后单独执行。
"""


import pytest
from app.core.config import settings
from app.services.external import tencent_ci


class _FakeClient:
    def __init__(self, detect_label_resp=None, audit_resp=None):
        self.detect_label_resp = detect_label_resp or {
            "CameraLabels": {"Labels": [
                {"Name": "截图", "Confidence": "82", "FirstCategory": "其他", "SecondCategory": "屏幕截图"},
                {"Name": "课程表", "Confidence": "67", "FirstCategory": "物品", "SecondCategory": "表格图表"},
            ]},
            "WebLabels": {"Labels": [{"Name": "截图", "Confidence": "82"}]},
        }
        self.audit_resp = audit_resp or {"JobsDetail": []}
        self.calls = []

    def ci_image_detect_label(self, **kwargs):
        self.calls.append(("detect", kwargs))
        return self.detect_label_resp

    def ci_auditing_image_batch(self, **kwargs):
        self.calls.append(("audit", kwargs))
        return self.audit_resp


def test_detect_label_parses_tags(monkeypatch):
    fake = _FakeClient()
    monkeypatch.setattr(tencent_ci, "_client", lambda: fake)
    tags = tencent_ci.image_detect_label("photos/2026/08/19/a.jpg")
    assert tags == ["截图", "课程表"]  # 去重 + 置信度 ≥50 过滤
    assert fake.calls[0][1]["Key"] == "photos/2026/08/19/a.jpg"
    assert fake.calls[0][1]["Bucket"] == settings.cos_bucket


def test_detect_label_empty_result(monkeypatch):
    fake = _FakeClient(detect_label_resp={"CameraLabels": {"Labels": []}})
    monkeypatch.setattr(tencent_ci, "_client", lambda: fake)
    assert tencent_ci.image_detect_label("k") == []


def test_audit_pass(monkeypatch):
    fake = _FakeClient(audit_resp={"JobsDetail": [{"PornInfo": {"HitFlag": "0"}}]})
    monkeypatch.setattr(tencent_ci, "_client", lambda: fake)
    result = tencent_ci.image_audit("k")
    assert result["pass"] is True
    assert result["labels"] == []


def test_audit_blocked(monkeypatch):
    fake = _FakeClient(
        audit_resp={
            "JobsDetail": [
                {"PornInfo": {"HitFlag": "1", "Label": "Porn"}, "IllegalInfo": {"HitFlag": "0"}}
            ]
        }
    )
    monkeypatch.setattr(tencent_ci, "_client", lambda: fake)
    result = tencent_ci.image_audit("k")
    assert result["pass"] is False
    assert "PornInfo:Porn" in result["labels"]


def test_audit_blocked_illegal(monkeypatch):
    fake = _FakeClient(
        audit_resp={"JobsDetail": [{"IllegalInfo": {"HitFlag": "1", "Label": "Illegal"}}]}
    )
    monkeypatch.setattr(tencent_ci, "_client", lambda: fake)
    assert tencent_ci.image_audit("k")["pass"] is False


def test_detect_label_confidence_filter(monkeypatch):
    fake = _FakeClient(detect_label_resp={
        "CameraLabels": {"Labels": [
            {"Name": "高置信", "Confidence": "90"},
            {"Name": "低置信", "Confidence": "10"},
        ]}
    })
    monkeypatch.setattr(tencent_ci, "_client", lambda: fake)
    assert tencent_ci.image_detect_label("k") == ["高置信"]


def test_unconfigured_raises(monkeypatch):
    monkeypatch.setattr(settings, "tencent_secret_id", "")
    monkeypatch.setattr(settings, "cos_bucket", "")
    with pytest.raises(RuntimeError):
        tencent_ci.image_detect_label("k")
