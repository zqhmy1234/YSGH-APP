"""台账 §7.12「静默降级显性化」的回归钉（2026-10-14）。

## 钉住什么

W1 记忆镜像 / W2 检索合并的降级**此前是不可见的**：缺令牌时要么只记一条 INFO
（生产里往往根本不被采集），要么**裸 `return` 什么都不记**。用户侧的形态是
「AI 说已记住、卡片也出了，但 App 时间轴 / 检索一片空白」，而运维那边连一条
告警都看不到——这正是本项目最贵的一类故障。

本文件钉三件事：

  1. 降级**一定**被计数（`yishu.degradation`，由 agent `/health` 暴露）；
  2. 降级**一定**打日志，且**首次 WARNING、之后 INFO**（既不隐形、又不刷屏）；
  3. 两侧令牌指纹**同一份算法**（`yishu.security.token_fingerprint`）——
     这是"两侧启动日志可以互相对账"的前提。
"""
from __future__ import annotations

import logging

import pytest
from tools import memory_tools as mt
from yishu import degradation
from yishu.security import backend_channel_token, token_fingerprint


@pytest.fixture(autouse=True)
def _clean_counters():
    """每条用例前后清计数——计数是模块级全局，否则用例执行顺序会影响断言。"""
    degradation.reset()
    yield
    degradation.reset()


def test_counter_counts_and_reports_first_occurrence():
    assert degradation.note("unit_probe") is True  # 首次
    assert degradation.note("unit_probe") is False  # 之后
    snapshot = degradation.snapshot()
    assert snapshot["unit_probe"] == 2
    assert snapshot["total"] == 2


def test_note_degrade_logs_warning_once_then_info(caplog):
    with caplog.at_level(logging.INFO, logger="yishu.observability"):
        mt._note_degrade("unit_probe")
        mt._note_degrade("unit_probe")
    levels = [r.levelno for r in caplog.records if "degrade_unit_probe" in r.getMessage()]
    assert levels == [logging.WARNING, logging.INFO]


def test_mirror_records_degradation_when_token_missing(monkeypatch):
    monkeypatch.setattr(mt, "_backend_base_and_token", lambda: ("http://test", ""))
    mt._mirror_memory_to_app(
        "m-1", "内容", mood=None, topics=None, people=None, location=None, category=None
    )
    assert degradation.snapshot().get("mirror_skipped_no_token") == 1


def test_mirror_records_degradation_when_module_disabled(monkeypatch):
    monkeypatch.setenv("AGENT_MIRROR_TO_APP", "0")
    mt._mirror_memory_to_app(
        "m-2", "内容", mood=None, topics=None, people=None, location=None, category=None
    )
    assert degradation.snapshot().get("mirror_api_disabled") == 1


def test_unmirror_records_degradation_when_token_missing(monkeypatch):
    monkeypatch.setattr(mt, "_backend_base_and_token", lambda: ("http://test", ""))
    mt._unmirror_memory_from_app("m-3")
    assert degradation.snapshot().get("unmirror_skipped_no_token") == 1


def test_backend_rag_search_records_degradation_when_token_missing(monkeypatch):
    monkeypatch.setenv("AGENT_BACKEND_SEARCH", "1")
    monkeypatch.delenv("AGENT_BACKEND_TOKEN", raising=False)
    monkeypatch.delenv("AGENT_SERVICE_TOKEN", raising=False)
    assert mt._backend_rag_search("香山", 5) == []
    assert degradation.snapshot().get("search_skipped_no_token") == 1


def test_backend_channel_token_prefers_dedicated_variable(monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_TOKEN", "shared")
    monkeypatch.delenv("AGENT_BACKEND_TOKEN", raising=False)
    assert backend_channel_token() == "shared"
    monkeypatch.setenv("AGENT_BACKEND_TOKEN", "dedicated")
    assert backend_channel_token() == "dedicated"


def test_token_fingerprint_is_stable_and_hides_value():
    assert token_fingerprint("") == "(空)"
    assert token_fingerprint("   ") == "(空)"
    assert token_fingerprint("abc") == token_fingerprint(" abc ")
    assert token_fingerprint("abc") != token_fingerprint("abd")
    assert len(token_fingerprint("abc")) == 8
    assert "abc" not in token_fingerprint("abc")


def test_health_exposes_degradation_and_channel_fingerprints():
    """`/health` 必须把"是否在降级"和"两侧是否同一把密钥"变成可探活的事实。"""
    import main

    resp = main.health()
    assert resp.status == "ok"
    # 指纹必须与 security 的算法**同一份**（两侧启动日志能对账的前提）
    assert resp.inbound_token_fp == token_fingerprint()
    assert resp.backend_token_fp == token_fingerprint(backend_channel_token())
    assert resp.degradation.get("total") == 0
