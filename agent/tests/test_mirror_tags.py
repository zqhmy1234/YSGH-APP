"""W3（2026-10-08）：镜像请求把 L3 topics 升出 extra → 契约 tags 字段

backend 只对 source=agent 把 tags 落到 contents.tags_json（App 详情页 chips
唯一数据源）；topics 此前只进 extra 时 App 完全看不见。本文件钉住请求体形态：
有 topics ⇒ body["tags"] 必须带上；无 topics ⇒ 不带该键（不发送垃圾字段）。
"""
from types import SimpleNamespace

import pytest
from tools import memory_tools as mt


class _CaptureClient:
    """替换 httpx.Client：捕获 POST 请求体，返回固定 200。"""

    last_json: dict = {}

    class _Resp(SimpleNamespace):
        @property
        def status_code(self):
            return 200

        def json(self):
            return {"data": {"id": "c-1"}}

    def __init__(self, **_k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, json=None, headers=None):
        type(self).last_json = json or {}
        return self._Resp(status_code=200, url=url, headers=headers)


@pytest.fixture()
def capture_mirror(monkeypatch):
    monkeypatch.setattr(mt, "_backend_base_and_token", lambda: ("http://test", "tok"))
    monkeypatch.setattr(mt, "_get_user_id", lambda: "mirror-user")
    # `import httpx` 在 _mirror_memory_to_app 函数体内执行 ⇒ 必须打在 httpx 模块
    # 本体上（函数每次执行都从 sys.modules 取同一模块对象），打 mt.httpx 会
    # AttributeError（函数内 import 不产生模块属性）。
    monkeypatch.setattr("httpx.Client", _CaptureClient)
    return _CaptureClient


def test_mirror_sends_topics_as_tags(capture_mirror):
    mt._mirror_memory_to_app(
        "m-1", "今天去了香山", mood="开心", topics=["旅行", "健康"],
        people=None, location=None, category="事件",
    )
    body = capture_mirror.last_json
    assert body["tags"] == ["旅行", "健康"]
    assert body["source"] == "agent"
    # extra 里仍保留全量（检索 payload 用），tags 是额外的"可见性"通道
    assert body["extra"]["topics"] == ["旅行", "健康"]


def test_mirror_without_topics_omits_tags_key(capture_mirror):
    mt._mirror_memory_to_app(
        "m-2", "随手一记", mood=None, topics=None,
        people=None, location=None, category=None,
    )
    assert "tags" not in capture_mirror.last_json
