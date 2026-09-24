"""微信主链收口（功能修复波 簇③ · 域⑨ D09-1/3/4/7/8/12）回归测试。

缺陷原貌（域⑨ 深审）：
  - **D09-1（P0）**：回调 `process_incoming(db, msg)` **不传 user_id**，而
    `user_wechat_bindings` 表**从未映射 ORM**（baseline 迁移把它当遗留空表 DROP）
    ⇒ F6 主链（微信收 → 下载媒体 → 建 Content → 入管线 → 产记忆）**整条不通**，
    回调只落一行 `wechat_messages`。`gateway` 明明解析了 `from_user`，全仓无消费方。
  - **D09-3（P1）**：微信端「删掉」只改 `wechat_messages.status`，**不触碰关联 Content**
    ⇒ 用户以为删了，记忆仍在（且不享受全局「软删 30 天」语义）。
  - **D09-4（P1）**：把「回调 Token」当「应用 Secret」用（两者在企微协议里不同源）
    ⇒ 生产 `gettoken` 必 40001 ⇒ 媒体链永久降级。
  - **D09-7（P1）**：async 端点里同步阻塞调媒体下载（最长 30s）⇒ 单事件循环被占，
    并发回调排队、放大企微 5s 超时重推。
  - **D09-8（P1）**：`WECOM_*` 环境变量**静默丢弃**（推荐注入名与 config 字段名不一致）
    ⇒ 按文档注入时 `_wechat_configured()` 恒 False、回调全 503 且无告警。
  - **D09-12（P2）**：验签通过但缺 `MsgId` ⇒ ValueError 逃逸为 **500**，企微持续重推。

前置：PG yishu 库（conftest 强制 fake 存储 + MOCK_EXTERNAL_AI=true）。
"""
from __future__ import annotations

import importlib
import time
import uuid

import pytest
from app.core.config import settings
from app.db.models import Content, DeletedLog, UserWechatBinding, WechatMessage
from app.db.session import SessionLocal
from app.services.wechat.crypto import encrypt
from app.services.wechat.signature import sign
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration

TOKEN = "QDG6eK"
AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
CORP_ID = "wx5823bf96d3bd56c7"


def _configure(monkeypatch) -> None:
    monkeypatch.setattr(settings, "wechat_corp_id", CORP_ID)
    monkeypatch.setattr(settings, "wechat_token", TOKEN)
    monkeypatch.setattr(settings, "wechat_encoding_aes_key", AES_KEY)


def _signed_post(client, plain_xml: str, *, nonce: str | None = None):
    """把明文 XML 加密并按企微协议签名后 POST 到回调端点"""
    nonce = nonce or uuid.uuid4().hex[:8]
    ts = str(int(time.time()))
    encrypted = encrypt(plain_xml, AES_KEY, CORP_ID)
    params = {
        "msg_signature": sign(TOKEN, ts, nonce, encrypted),
        "timestamp": ts,
        "nonce": nonce,
    }
    body = f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"
    return client.post("/api/v1/wechat/callback", params=params, content=body)


def _xml(msg_type: str, from_user: str, msg_id: str, **extra: str) -> str:
    parts = {
        "ToUserName": "toUser",
        "FromUserName": from_user,
        "CreateTime": str(int(time.time())),
        "MsgType": msg_type,
        **extra,
        "MsgId": msg_id,
    }
    inner = "".join(f"<{k}><![CDATA[{v}]]></{k}>" for k, v in parts.items() if v is not None)
    return f"<xml>{inner}</xml>"


def _client():
    from app.main import app
    from fastapi.testclient import TestClient

    return TestClient(app)


def _auth_headers(client) -> tuple[str, dict]:
    """登录 → (user_id, headers)；user_id 经 GET /users/me 取（登录出参只有 token 对）"""
    r = client.post("/api/v1/auth/wechat", json={"code": f"wxb-{uuid.uuid4().hex[:8]}", "device_id": "d"})
    assert r.status_code == 200, r.text
    headers = {"Authorization": "Bearer " + r.json()["data"]["access_token"]}
    me = client.get("/api/v1/users/me", headers=headers)
    assert me.status_code == 200, me.text
    return me.json()["data"]["id"], headers


@pytest.fixture()
def cleanup_wechat():
    """清理本文件造的微信域数据（消息 / 绑定 / 内容）"""
    yield
    db = SessionLocal()
    try:
        db.execute(sa_delete(Content).where(Content.source == "wechat"))
        db.execute(sa_delete(WechatMessage).where(WechatMessage.msg_id.like("wxb-%")))
        db.execute(sa_delete(UserWechatBinding).where(UserWechatBinding.openid.like("wxb-%")))
        db.commit()
    finally:
        db.close()


# ---------------------------------------------------------------------------
# D09-1：绑定存在 ⇒ 回调主链贯通（修复前：只落一行消息，无 Content）
# ---------------------------------------------------------------------------


def test_unbound_callback_records_message_only(monkeypatch, cleanup_wechat):
    """未绑定 openid：只落 wechat_messages（user_id=None），不建内容（语义保持）"""
    _configure(monkeypatch)
    client = _client()
    openid, msg_id = f"wxb-unbound-{uuid.uuid4().hex[:6]}", f"wxb-{uuid.uuid4().hex[:8]}"
    r = _signed_post(client, _xml("text", openid, msg_id, Content="未绑定用户的碎碎念"))
    assert r.status_code == 200 and r.text == "success", r.text

    db = SessionLocal()
    try:
        row = db.query(WechatMessage).filter(WechatMessage.msg_id == msg_id).one()
        assert row.user_id is None
        assert db.query(Content).filter(Content.source == "wechat").count() == 0
    finally:
        db.close()


def test_bound_callback_creates_content(monkeypatch, cleanup_wechat):
    """**D09-1 主链回归**：绑定后回调必须建 Content（修复前恒不建）"""
    _configure(monkeypatch)
    client = _client()
    user_id, headers = _auth_headers(client)
    openid = f"wxb-open-{uuid.uuid4().hex[:6]}"
    r = client.post(
        "/api/v1/wechat/bind", json={"openid": openid, "channel": "wechat_kf"}, headers=headers
    )
    assert r.status_code == 200, r.text

    msg_id = f"wxb-{uuid.uuid4().hex[:8]}"
    text = f"绑定后的这条要进记忆 {uuid.uuid4().hex[:6]}"
    r2 = _signed_post(client, _xml("text", openid, msg_id, Content=text))
    assert r2.status_code == 200, r2.text

    db = SessionLocal()
    try:
        row = db.query(WechatMessage).filter(WechatMessage.msg_id == msg_id).one()
        assert str(row.user_id) == str(user_id), "回调应带上绑定解析出的 user_id"
        content = db.query(Content).filter(Content.source == "wechat", Content.text == text).one()
        assert str(content.user_id) == str(user_id)
        assert content.extra["wechat_msg_id"] == msg_id, "文本内容也要留反查锚点（D09-3 前置）"
    finally:
        db.close()


def test_bound_image_callback_downloads_media(monkeypatch, cleanup_wechat):
    """绑定后**图片**回调：下载媒体 → 落 wechat/ 键 → 建 photo Content（F6 主链）"""
    _configure(monkeypatch)
    client = _client()
    user_id, headers = _auth_headers(client)
    openid = f"wxb-media-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/wechat/bind", json={"openid": openid}, headers=headers)

    msg_id = f"wxb-{uuid.uuid4().hex[:8]}"
    r = _signed_post(
        client, _xml("image", openid, msg_id, PicUrl="https://x/y.jpg", MediaId="MEDIA_PROBE")
    )
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        content = db.query(Content).filter(Content.source == "wechat", Content.content_type == "photo").first()
        assert content is not None, "绑定后图片回调必须建 Content（D09-1）"
        assert str(content.user_id) == str(user_id)
        assert content.cos_key.startswith(f"wechat/{user_id}/")
    finally:
        db.close()


def test_callback_missing_msgid_returns_403_not_500(monkeypatch, cleanup_wechat):
    """D09-12：验签通过但缺 MsgId ⇒ 403 WECHAT_002（原为 500，企微会无限重推）"""
    _configure(monkeypatch)
    client = _client()
    openid = f"wxb-nomsgid-{uuid.uuid4().hex[:6]}"
    xml = _xml("text", openid, "", Content="缺 MsgId")  # 空 MsgId ⇒ 解析后 None
    r = _signed_post(client, xml)
    assert r.status_code == 403, f"应为 403，实际 {r.status_code} {r.text[:200]}"
    assert r.json()["code"] == "WECHAT_002"


def test_callback_runs_off_event_loop():
    """D09-7 防漂移：回调处理体必须经 `run_in_threadpool`（不在事件循环里同步阻塞）"""
    import inspect

    api_wechat = importlib.import_module("app.api.wechat")
    src = inspect.getsource(api_wechat.wechat_callback)
    assert "run_in_threadpool" in src, "回调又回到事件循环里同步调用（D09-7 复发）"
    assert "process_incoming" not in src, "处理体应封装进 _ingest_callback_message"


# ---------------------------------------------------------------------------
# 绑定端点：幂等 / 防劫持 / 渠道白名单 / 解绑归属
# ---------------------------------------------------------------------------


def test_bind_idempotent_and_conflict_409(cleanup_wechat):
    """同用户重复绑定幂等；他人 openid 冲突 → 409 WECHAT_004（不静默换绑）"""
    client = _client()
    _uid_a, headers_a = _auth_headers(client)
    _uid_b, headers_b = _auth_headers(client)
    openid = f"wxb-share-{uuid.uuid4().hex[:6]}"

    r1 = client.post("/api/v1/wechat/bind", json={"openid": openid}, headers=headers_a)
    assert r1.status_code == 200 and r1.json()["data"]["bound"] is True
    r2 = client.post("/api/v1/wechat/bind", json={"openid": openid}, headers=headers_a)
    assert r2.status_code == 200, "同用户重复绑定应幂等"

    r3 = client.post("/api/v1/wechat/bind", json={"openid": openid}, headers=headers_b)
    assert r3.status_code == 409, f"他人 openid 应 409，实际 {r3.status_code}"
    assert r3.json()["code"] == "WECHAT_004"


def test_bind_rejects_unknown_channel(cleanup_wechat):
    """渠道白名单（WECHAT_BINDING_CHANNELS）：未知渠道 → 422 WECHAT_005"""
    client = _client()
    _uid, headers = _auth_headers(client)
    r = client.post(
        "/api/v1/wechat/bind",
        json={"openid": f"wxb-ch-{uuid.uuid4().hex[:6]}", "channel": "telegram"},
        headers=headers,
    )
    assert r.status_code == 422
    assert r.json()["code"] == "WECHAT_005"


def test_bind_requires_auth(cleanup_wechat):
    """绑定必须鉴权（否则任何人可把我微信绑定到他的账号）"""
    r = _client().post("/api/v1/wechat/bind", json={"openid": "wxb-anon"})
    assert r.status_code == 401


def test_list_and_unbind_only_own(cleanup_wechat):
    """列表只含本人绑定；解绑他人绑定 → 404（不泄露）"""
    client = _client()
    _uid_a, headers_a = _auth_headers(client)
    _uid_b, headers_b = _auth_headers(client)
    openid_a = f"wxb-mine-{uuid.uuid4().hex[:6]}"
    openid_b = f"wxb-yours-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/wechat/bind", json={"openid": openid_a}, headers=headers_a)
    client.post("/api/v1/wechat/bind", json={"openid": openid_b}, headers=headers_b)

    data = client.get("/api/v1/wechat/bindings", headers=headers_a).json()["data"]
    assert [b["openid"] for b in data["bindings"]] == [openid_a]

    assert client.delete(
        "/api/v1/wechat/bindings", params={"openid": openid_b}, headers=headers_a
    ).status_code == 404
    assert client.delete(
        "/api/v1/wechat/bindings", params={"openid": openid_a}, headers=headers_a
    ).status_code == 200
    assert client.get("/api/v1/wechat/bindings", headers=headers_a).json()["data"]["bindings"] == []


# ---------------------------------------------------------------------------
# D09-3：微信端「删掉」也要走全局软删语义
# ---------------------------------------------------------------------------


def test_soft_delete_by_msg_soft_deletes_linked_content(monkeypatch, cleanup_wechat):
    """微信「删掉」⇒ 关联 Content 走同一软删路径（权威投影 + 审计日志）"""
    _configure(monkeypatch)
    client = _client()
    user_id, headers = _auth_headers(client)
    openid = f"wxb-del-{uuid.uuid4().hex[:6]}"
    client.post("/api/v1/wechat/bind", json={"openid": openid}, headers=headers)

    msg_id = f"wxb-{uuid.uuid4().hex[:8]}"
    text = f"待删除的微信记忆 {uuid.uuid4().hex[:6]}"
    assert _signed_post(client, _xml("text", openid, msg_id, Content=text)).status_code == 200

    db = SessionLocal()
    try:
        content = db.query(Content).filter(Content.source == "wechat", Content.text == text).one()
        content_id = str(content.id)
        assert content.deleted_at is None
    finally:
        db.close()

    r = client.post("/api/v1/wechat/delete", params={"msg_id": msg_id}, headers=headers)
    assert r.status_code == 200, r.text

    db = SessionLocal()
    try:
        content = db.get(Content, content_id)
        assert content.deleted_at is not None, "D09-3：关联内容必须被软删（原为完全不动）"
        assert content.deleted_by == user_id
        log = db.query(DeletedLog).filter(DeletedLog.content_id == content_id).one()
        assert log.cleanup_status == "pending", "审计日志必须入队 ⇒ 30 天后才会真删"
    finally:
        db.close()


# ---------------------------------------------------------------------------
# D09-8 / D09-4：配置别名与「回调 Token ≠ 应用 Secret」
# ---------------------------------------------------------------------------


def test_wecom_env_aliases_are_read(monkeypatch):
    """D09-8：按文档注入 WECOM_* 也必须生效（此前 extra=ignore 静默丢弃）"""
    from app.core.config import Settings

    monkeypatch.setenv("WECOM_CORP_ID", "corp-from-wecom")
    monkeypatch.setenv("WECOM_TOKEN", "token-from-wecom")
    monkeypatch.setenv("WECOM_ENCODING_AES_KEY", "aes-from-wecom")
    monkeypatch.setenv("WECOM_CORP_SECRET", "secret-from-wecom")
    s = Settings()
    assert s.wechat_corp_id == "corp-from-wecom"
    assert s.wechat_token == "token-from-wecom"
    assert s.wechat_encoding_aes_key == "aes-from-wecom"
    assert s.wechat_corp_secret == "secret-from-wecom"


def test_corp_secret_falls_back_to_token_with_warning(monkeypatch, caplog):
    """D09-4：未配 WECHAT_CORP_SECRET 时回退 token（兼容），但要留明确 WARNING"""
    wx = importlib.import_module("app.services.wechat.service")
    monkeypatch.setattr(settings, "wechat_corp_secret", "")
    monkeypatch.setattr(settings, "wechat_token", "callback-token-value")
    with caplog.at_level("WARNING"):
        assert wx._corp_secret() == "callback-token-value"
    assert any("WECHAT_CORP_SECRET" in r.message for r in caplog.records)

    monkeypatch.setattr(settings, "wechat_corp_secret", "real-app-secret")
    assert wx._corp_secret() == "real-app-secret"


def test_gettoken_uses_app_secret_not_callback_token(monkeypatch):
    """D09-4：gettoken 的 corpsecret 必须是**应用 Secret**（原实现错用回调 Token）"""
    wx = importlib.import_module("app.services.wechat.service")
    monkeypatch.setattr(settings, "mock_external_ai", False)
    monkeypatch.setattr(settings, "wechat_corp_id", "corp-x")
    monkeypatch.setattr(settings, "wechat_token", "callback-token")
    monkeypatch.setattr(settings, "wechat_corp_secret", "app-secret")
    wx._token_cache.clear()
    captured: dict = {}

    class _Resp:
        status_code = 200

        def raise_for_status(self):  # noqa: D102
            return None

        def json(self):  # noqa: D102
            return {"errcode": 0, "access_token": "tok", "expires_in": 7200}

    def _fake_get(url, params, timeout):
        captured["params"] = params
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "get", _fake_get)
    try:
        assert wx._corp_access_token() == "tok"
        assert captured["params"]["corpsecret"] == "app-secret"
        assert captured["params"]["corpid"] == "corp-x"
    finally:
        wx._token_cache.clear()


def test_helpful_verdict_single_source():
    """绑定解析的"未绑定"必须返回 None（而非抛错）——企微回调要求始终 200"""
    from app.services.wechat.binding import normalize_openid, resolve_user_id

    assert normalize_openid("   ") is None
    db = SessionLocal()
    try:
        assert resolve_user_id(db, None) is None
        assert resolve_user_id(db, f"wxb-not-bound-{uuid.uuid4().hex[:6]}") is None
    finally:
        db.close()
