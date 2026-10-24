"""B-02 回归：推送通道从 mock 变成「可配置的真实通道」（2026-10-24 · 待办总账 B-02）

## 缺陷

`set_push_channel()` **全仓零调用**、`MockPushChannel` 是唯一实现 ⇒ 每日复盘 / 语音完成 /
胶囊到期等 push 消息**真实落库**，但手机端**不会有任何系统通知**（用户只能自己打开 App
进消息页才看到；真库实测 589 条消息里 491 条未读）。最坏的地方是它**静默**：
「推送成功」与「根本没有推送通道」在日志里长得几乎一样。

## 本批口径（不谎称已闭环）

- 真接厂商 SDK 需要 AppID + 证书，本仓**一份都没有**（客户端 `manifest.json` 无 push 模块）
  ⇒ 本批做的是**可配置的真实通道骨架** `WebhookPushChannel`：配 URL 就真 POST，
  未配置就**显式 WARNING + 计数**，绝不伪装成功；
- 端上弹出系统通知仍需客户端接 push SDK，**本批未接**；
- `main.py` lifespan 用 `set_push_channel()` 接线（顺带清掉 B-06①「零调用」）。

## 覆盖

1. 未配置 → 不抛错、不伪装成功：`skipped_unconfigured` 增、`delivered` 不增、打 WARNING
2. 已配置 → 真的走 HTTP（httpx 桩），断言 URL / 方法 / 幂等键 / 鉴权头 / 超时
3. 投递失败（HTTP 4xx/5xx、网络异常）→ **全吞**，消息落库不受影响（旁路铁律）
4. `main.py` 启动确实调了 `set_push_channel`（B-06① 接线缺口）
5. `create_message(channel="push")` 端到端走当前注入的通道
"""
import json
import logging

import pytest
from app.db.models import Message
from app.services import notify as notify_mod
from app.services.notify import (
    MockPushChannel,
    WebhookPushChannel,
    build_push_channel_from_settings,
    create_message,
    get_push_channel,
    push_delivery_stats,
    reset_push_delivery_stats,
    set_push_channel,
)
from sqlalchemy import select

pytestmark = pytest.mark.integration

_WEBHOOK = "https://example.invalid/push/endpoint"


# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_push_channel():
    """每个用例前后复位通道与计数（全局单例，跨用例必须隔离）"""
    set_push_channel(None)
    reset_push_delivery_stats()
    yield
    set_push_channel(None)
    reset_push_delivery_stats()


@pytest.fixture()
def http_calls(monkeypatch):
    """httpx.post 桩：记录调用参数，返回可配置状态码

    只替换 `httpx.post`（模块级函数），不动被测代码 —— 被测代码照旧 `import httpx`
    再调用，与生产路径逐字一致。
    """

    class _Resp:
        def __init__(self, status_code: int) -> None:
            self.status_code = status_code

    state = {"status": 200, "calls": []}

    def _fake_post(url, **kwargs):
        state["calls"].append({"url": url, **kwargs})
        return _Resp(state["status"])

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)
    return state


def _msg(db, user_id) -> Message:
    """造一条真实 messages 行（不经通道，避免测试自己的投递记账干扰断言）"""
    m = Message(
        user_id=user_id,
        channel="push",
        msg_type="daily_review",
        title="今日回顾",
        body="今天记下了 3 条记忆。",
        payload={"day": "2026-10-24"},
    )
    db.add(m)
    db.flush([m])
    return m


# ---------------------------------------------------------------------------
# 1. 未配置：不伪装成功
# ---------------------------------------------------------------------------


class TestUnconfigured:
    def test_unconfigured_warns_and_does_not_claim_success(self, db_user, caplog):
        """未配置 webhook → WARNING + skipped_unconfigured 计数，**不抛错**

        这条是本缺陷的核心：旧 MockPushChannel 只打 INFO，事后无从区分
        「推了」与「没有通道」。现在必须能在计数上区分。
        """
        db, user = db_user
        ch = WebhookPushChannel(url="", token="")
        m = _msg(db, user.id)

        with caplog.at_level(logging.WARNING, logger="yishu.notify"):
            ch.send(user_id=str(user.id), message=m)

        stats = push_delivery_stats()
        assert stats["attempted"] == 1
        assert stats["skipped_unconfigured"] == 1
        assert stats["delivered"] == 0, "未配置绝不能计入 delivered（伪装成功）"
        assert stats["failed"] == 0, "未配置不算投递失败（是没配，不是坏了）"
        assert any("未配置" in r.message for r in caplog.records), "必须显式 WARNING"

    def test_unconfigured_channel_is_not_mock(self, db_user):
        """build_push_channel_from_settings 未配置时也**不退回 Mock**

        退回 mock 就是原缺陷的形态（静默）；必须返回未配置态的 WebhookPushChannel，
        让"没配"在计数与日志上可见。
        """
        ch = build_push_channel_from_settings()
        assert isinstance(ch, WebhookPushChannel)
        assert not isinstance(ch, MockPushChannel)
        assert ch.configured is False

    def test_unconfigured_still_saves_message(self, db_user):
        """未配置时消息必须**照常落库**（推送是旁路，不能影响消息中心）"""
        db, user = db_user
        set_push_channel(WebhookPushChannel(url=""))
        before = len(db.execute(select(Message).where(Message.user_id == user.id)).scalars().all())
        create_message(db, user.id, "push", "voice_done", "语音已整理好", "可以来看看。", {})
        db.commit()
        after = db.execute(select(Message).where(Message.user_id == user.id)).scalars().all()
        assert len(after) == before + 1
        assert push_delivery_stats()["delivered"] == 0


# ---------------------------------------------------------------------------
# 2. 已配置：真的走 HTTP
# ---------------------------------------------------------------------------


class TestConfigured:
    def test_configured_sends_real_http(self, db_user, http_calls):
        """已配置 → 真的 POST（断言 URL / 幂等键 / 鉴权头 / 超时）"""
        db, user = db_user
        ch = WebhookPushChannel(url=_WEBHOOK, token="tok-abc", timeout=2.5)  # noqa: S106 — 测试合成值
        assert ch.configured is True
        m = _msg(db, user.id)

        ch.send(user_id=str(user.id), message=m)

        assert len(http_calls["calls"]) == 1
        call = http_calls["calls"][0]
        assert call["url"] == _WEBHOOK
        assert call["timeout"] == 2.5, "必须带超时（旁路铁律）"
        assert call["headers"]["Authorization"] == "Bearer tok-abc"
        body = call["json"]
        assert body["message_id"] == m.id, "幂等键 = message.id（接收端去重依据）"
        assert body["user_id"] == str(user.id)
        assert body["msg_type"] == "daily_review"
        assert body["title"] == "今日回顾"

        stats = push_delivery_stats()
        assert stats["delivered"] == 1
        assert stats["failed"] == 0

    def test_no_token_omits_authorization_header(self, db_user, http_calls):
        """token 留空则不带头（不发送空的 Bearer）"""
        db, user = db_user
        ch = WebhookPushChannel(url=_WEBHOOK, token="")
        ch.send(user_id=str(user.id), message=_msg(db, user.id))
        assert "Authorization" not in http_calls["calls"][0]["headers"]

    def test_reads_from_settings(self, db_user, monkeypatch, http_calls):
        """配置从 settings 读（不硬编码）——B-02「可配置」的判据"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "push_webhook_url", _WEBHOOK)
        monkeypatch.setattr(settings, "push_webhook_token", "from-settings")
        monkeypatch.setattr(settings, "push_webhook_timeout_s", 7.5)

        ch = WebhookPushChannel()  # 不传参 → 走配置
        assert ch.configured is True
        assert ch.timeout == 7.5
        db, user = db_user
        ch.send(user_id=str(user.id), message=_msg(db, user.id))
        assert http_calls["calls"][0]["url"] == _WEBHOOK
        assert http_calls["calls"][0]["headers"]["Authorization"] == "Bearer from-settings"

    def test_invalid_timeout_falls_back(self, monkeypatch):
        """0/负数超时会让 httpx 立即超时 ⇒ 必须防呆退化（不静默变 0）"""
        from app.core.config import settings

        monkeypatch.setattr(settings, "push_webhook_url", _WEBHOOK)
        monkeypatch.setattr(settings, "push_webhook_timeout_s", 0)
        assert WebhookPushChannel().timeout == 5.0


# ---------------------------------------------------------------------------
# 3. 失败全吞（旁路铁律）
# ---------------------------------------------------------------------------


class TestFailureIsSwallowed:
    def test_http_error_does_not_raise(self, db_user, http_calls):
        """HTTP 500 → 不抛、计数为 failed、消息仍落库"""
        db, user = db_user
        http_calls["status"] = 500
        set_push_channel(WebhookPushChannel(url=_WEBHOOK))
        create_message(db, user.id, "push", "daily_review", "今日回顾", "内容", {})
        db.commit()
        stats = push_delivery_stats()
        assert stats["failed"] == 1
        assert stats["delivered"] == 0
        assert "HTTP 500" in (stats["last_error"] or "")
        rows = db.execute(select(Message).where(Message.user_id == user.id)).scalars().all()
        assert len(rows) == 1, "推送失败绝不能影响消息落库"

    def test_network_exception_does_not_raise(self, db_user, monkeypatch):
        """网络异常/超时/任意异常 → 全吞（绝不外泄到消息主链路）"""
        db, user = db_user

        def boom(url, **kwargs):
            raise ConnectionError("webhook unreachable")

        import httpx

        monkeypatch.setattr(httpx, "post", boom)
        set_push_channel(WebhookPushChannel(url=_WEBHOOK))
        # 不应抛
        create_message(db, user.id, "push", "voice_done", "语音已整理好", "内容", {})
        db.commit()
        stats = push_delivery_stats()
        assert stats["failed"] == 1
        assert "ConnectionError" in (stats["last_error"] or "")
        rows = db.execute(select(Message).where(Message.user_id == user.id)).scalars().all()
        assert len(rows) == 1

    def test_unexpected_error_does_not_raise(self, db_user, monkeypatch):
        """即使桩本身抛非网络异常（如编码错误），也必须被吞掉"""
        db, user = db_user

        def boom(url, **kwargs):
            raise ValueError("unexpected serializer issue")

        import httpx

        monkeypatch.setattr(httpx, "post", boom)
        ch = WebhookPushChannel(url=_WEBHOOK)
        ch.send(user_id=str(user.id), message=_msg(db, user.id))  # 不抛
        assert push_delivery_stats()["failed"] == 1


# ---------------------------------------------------------------------------
# 4. 接线缺口 B-06①：set_push_channel 必须真的有调用方
# ---------------------------------------------------------------------------


class TestWiring:
    def test_lifespan_calls_set_push_channel(self, monkeypatch):
        """`main.py` lifespan 启动时必须调 set_push_channel（B-06① 零调用的判据）

        这是 B-02 与 B-06① 的交点：通道写得再好，没有调用方就仍是死代码。
        """
        calls: list = []

        def _spy(channel):
            calls.append(channel)

        monkeypatch.setattr(notify_mod, "set_push_channel", _spy)
        # main.lifespan 内部是 `from app.services.notify import ...`（延迟导入），
        # 故 patch 模块属性即可命中。
        from app.main import app
        from fastapi.testclient import TestClient

        with TestClient(app):  # 触发 lifespan 启动/关闭
            pass
        assert calls, "lifespan 未调用 set_push_channel —— B-06① 零调用复发"
        assert isinstance(calls[0], WebhookPushChannel)

    def test_set_push_channel_replaces_channel(self):
        """注入确实生效（get_push_channel 返回注入的实例；None 复位 mock）"""
        ch = WebhookPushChannel(url=_WEBHOOK)
        set_push_channel(ch)
        assert get_push_channel() is ch
        set_push_channel(None)
        assert isinstance(get_push_channel(), MockPushChannel)


# ---------------------------------------------------------------------------
# 5. 端到端：create_message 走当前注入的通道
# ---------------------------------------------------------------------------


def test_create_message_dispatches_to_injected_channel(db_user, http_calls):
    """create_message(channel="push") → 当前注入的 WebhookPushChannel 真的被调用"""
    db, user = db_user
    set_push_channel(WebhookPushChannel(url=_WEBHOOK))
    m = create_message(db, user.id, "push", "daily_review", "今日回顾", "内容", {"k": 1})
    db.commit()

    assert len(http_calls["calls"]) == 1
    assert http_calls["calls"][0]["json"]["message_id"] == m.id
    assert push_delivery_stats()["delivered"] == 1


def test_in_app_channel_never_pushes(db_user, http_calls):
    """in_app 消息不走推送通道（只有 push 分支投递）"""
    db, user = db_user
    set_push_channel(WebhookPushChannel(url=_WEBHOOK))
    create_message(db, user.id, "in_app", "care_followup", "想陪你聊聊", "内容", {})
    db.commit()
    assert http_calls["calls"] == []
    assert push_delivery_stats()["attempted"] == 0


def test_payload_is_json_serializable(db_user):
    """投递体必须可 JSON 序列化（含 sent_at=None 的新消息）"""
    db, user = db_user
    ch = WebhookPushChannel(url=_WEBHOOK)
    body = ch._payload(str(user.id), _msg(db, user.id))
    json.dumps(body)  # 不抛即可
    assert body["channel"] == "push"
