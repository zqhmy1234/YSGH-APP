"""企微回调协议测试（path/90931 官方凭证 + 协议 1:1）

覆盖：
  - AES-256-CBC 加解密往返（官方测试凭证）
  - SHA1 签名 + 篡改拒绝
  - URL 验证流程（echostr 解密回显）
  - 收包（text/image/voice 三类型 XML → 消息 dict）
  - 错误签名/错误 receiveid 拒绝
  - msg_id 幂等（重复回调只入一次）
  - API 冒烟（GET 验证 + POST 收包）
"""
import uuid

import pytest
from app.core.config import settings
from app.db.models import Content, WechatMessage
from app.db.session import SessionLocal
from app.services.wechat.crypto import decrypt, encrypt
from app.services.wechat.gateway import handle_message, verify_url
from app.services.wechat.service import process_incoming
from app.services.wechat.signature import sign, verify
from sqlalchemy import delete as sa_delete

pytestmark = pytest.mark.integration

TOKEN = "QDG6eK"
AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
CORP_ID = "wx5823bf96d3bd56c7"

TEXT_XML = (
    "<xml>"
    "<ToUserName><![CDATA[toUser]]></ToUserName>"
    "<FromUserName><![CDATA[fromUser]]></FromUserName>"
    "<CreateTime>1348831860</CreateTime>"
    "<MsgType><![CDATA[text]]></MsgType>"
    "<Content><![CDATA[this is a test]]></Content>"
    "<MsgId>1234567890</MsgId>"
    "<AgentID>1</AgentID>"
    "</xml>"
)


def _wrap(encrypted: str) -> str:
    return f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"


def _params(encrypted: str, ts: str = "1409659589", nonce: str = "1372623149") -> dict:
    return {
        "msg_signature": sign(TOKEN, ts, nonce, encrypted),
        "timestamp": ts,
        "nonce": nonce,
    }


def test_crypto_roundtrip():
    """加解密往返：官方凭证下明文原样还原 + receiveid 一致"""
    msg, rid = decrypt(encrypt(TEXT_XML, AES_KEY, CORP_ID), AES_KEY)
    assert msg == TEXT_XML
    assert rid == CORP_ID


def test_crypto_wrong_key_rejected():
    """错误 AES key → 拒包（确定性断言）

    修复（原断言必然失败）：43 位 base64 补一个 '=' 时末位字符只贡献被丢弃的
    低 4 位——原"错误 key"('...2C' vs '...2D') 解码出相同 32 字节，解密必然成功。
    确定性语义：错误 key 要么解密抛错，要么产出结构与原文必然不同
    （receiveid 不匹配，调用侧拒包）。
    """
    encrypted = encrypt(TEXT_XML, AES_KEY, CORP_ID)
    wrong_key = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2D"[::-1]  # 反转，解码字节必然不同
    try:
        msg, rid = decrypt(encrypted, wrong_key)
    except Exception:
        return  # 结构/填充损坏 → 拒绝
    assert (msg, rid) != (TEXT_XML, CORP_ID), "错误 key 不可能还原出原文（PRP 性质）"


def test_signature_verify_and_tamper():
    """签名一致通过；篡改 encrypt → 验签失败"""
    encrypted = encrypt(TEXT_XML, AES_KEY, CORP_ID)
    p = _params(encrypted)
    assert verify(TOKEN, p["timestamp"], p["nonce"], encrypted, p["msg_signature"]) is True
    assert verify(TOKEN, p["timestamp"], p["nonce"], encrypted + "x", p["msg_signature"]) is False


def test_verify_url_flow():
    """URL 验证：echostr 解密回显"""
    echostr = encrypt("1616140317555161061", AES_KEY, CORP_ID)
    p = _params(echostr)
    plain = verify_url(TOKEN, AES_KEY, CORP_ID, p["msg_signature"], p["timestamp"], p["nonce"], echostr)
    assert plain == "1616140317555161061"


def test_handle_message_text():
    """收包：加密 XML → 解析出 text 消息（含 msg_id）"""
    encrypted = encrypt(TEXT_XML, AES_KEY, CORP_ID)
    p = _params(encrypted)
    msg = handle_message(TOKEN, AES_KEY, CORP_ID, p["msg_signature"], p["timestamp"], p["nonce"], _wrap(encrypted))
    assert msg["msg_type"] == "text"
    assert msg["content"] == "this is a test"
    assert msg["msg_id"] == "1234567890"


def test_handle_message_image_voice():
    """收包：image/voice 类型解析"""
    for msg_type, field in (("image", "MEDIA_IMG_01"), ("voice", "MEDIA_VO_01")):
        if msg_type == "image":
            plain = (
                "<xml><MsgType><![CDATA[image]]></MsgType>"
                "<PicUrl><![CDATA[http://x/p.jpg]]></PicUrl>"
                f"<MediaId><![CDATA[{field}]]></MediaId>"
                "<MsgId>111</MsgId><AgentID>1</AgentID></xml>"
            )
        else:
            plain = (
                "<xml><MsgType><![CDATA[voice]]></MsgType>"
                f"<MediaId><![CDATA[{field}]]></MediaId>"
                "<Format><![CDATA[amr]]></Format>"
                "<MsgId>222</MsgId><AgentID>1</AgentID></xml>"
            )
        encrypted = encrypt(plain, AES_KEY, CORP_ID)
        p = _params(encrypted)
        msg = handle_message(TOKEN, AES_KEY, CORP_ID, p["msg_signature"], p["timestamp"], p["nonce"], _wrap(encrypted))
        assert msg["msg_type"] == msg_type
        assert msg["media_id"] == field


def test_handle_message_bad_signature_rejected():
    """伪造回调：签名不匹配 → 拒绝"""
    encrypted = encrypt(TEXT_XML, AES_KEY, CORP_ID)
    with pytest.raises(ValueError):
        handle_message(TOKEN, AES_KEY, CORP_ID, "deadbeef", "1", "2", _wrap(encrypted))


def test_handle_message_wrong_corpid_rejected():
    """receiveid 不匹配（回调发给别的企业）→ 拒绝"""
    encrypted = encrypt(TEXT_XML, AES_KEY, "other-corpid")
    p = _params(encrypted)
    with pytest.raises(ValueError):
        handle_message(TOKEN, AES_KEY, CORP_ID, p["msg_signature"], p["timestamp"], p["nonce"], _wrap(encrypted))


def test_process_incoming_idempotent():
    """msg_id 幂等：重复回调只入一次"""
    db = SessionLocal()
    try:
        msg = {"msg_id": f"wx-{uuid.uuid4().hex[:12]}", "msg_type": "text", "content": "明天记得取快递"}
        r1 = process_incoming(db, msg)
        assert r1["status"] == "created"
        r2 = process_incoming(db, msg)
        assert r2["status"] == "duplicate"
        rows = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).all()
        assert len(rows) == 1
    finally:
        db.execute(sa_delete(WechatMessage).where(WechatMessage.msg_id == msg["msg_id"]))
        db.execute(sa_delete(Content).where(Content.source == "wechat"))
        db.commit()
        db.close()


def test_process_incoming_concurrent_same_msg_id_no_500():
    """R2#13 竞态修复：并发同 msg_id 回调不 500（ON CONFLICT DO NOTHING 原子幂等）

    两会话同时处理同一回调：一个 created，另一个 rowcount=0 → duplicate，仅一行入库。
    """
    import threading

    from app.db.session import SessionLocal

    msg = {"msg_id": f"wx-race-{uuid.uuid4().hex[:10]}", "msg_type": "text", "content": "并发消息"}
    results: dict = {}

    def worker(n: int):
        s = SessionLocal()
        try:
            r = process_incoming(s, msg)
            results[n] = r["status"]
        except Exception as exc:  # noqa: BLE001 —— 记录逃逸异常（不应发生）
            results[n] = exc
        finally:
            s.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(v in ("created", "duplicate") for v in results.values()), results

    db = SessionLocal()
    try:
        rows = db.query(WechatMessage).filter(WechatMessage.msg_id == msg["msg_id"]).all()
        assert len(rows) == 1, "并发同 msg_id 只应有一行入库"
    finally:
        db.execute(sa_delete(WechatMessage).where(WechatMessage.msg_id == msg["msg_id"]))
        db.execute(sa_delete(Content).where(Content.source == "wechat"))
        db.commit()
        db.close()


def test_wechat_api_smoke(monkeypatch):
    """API 冒烟：GET 验证 URL + POST 收包（配置测试凭证后）"""
    from app.main import app
    from fastapi.testclient import TestClient

    monkeypatch.setattr(settings, "wechat_corp_id", CORP_ID)
    monkeypatch.setattr(settings, "wechat_token", TOKEN)
    monkeypatch.setattr(settings, "wechat_encoding_aes_key", AES_KEY)

    db = SessionLocal()
    client = TestClient(app)
    try:
        echostr = encrypt("1616140317555161061", AES_KEY, CORP_ID)
        p = _params(echostr)
        r = client.get("/api/v1/wechat/callback", params={**p, "echostr": echostr})
        assert r.status_code == 200
        assert r.text == "1616140317555161061"

        encrypted = encrypt(TEXT_XML, AES_KEY, CORP_ID)
        p2 = _params(encrypted)
        r2 = client.post("/api/v1/wechat/callback", params=p2, content=_wrap(encrypted))
        assert r2.status_code == 200
        assert r2.text == "success"
    finally:
        db.execute(sa_delete(WechatMessage).where(WechatMessage.msg_id == "1234567890"))
        db.execute(sa_delete(Content).where(Content.source == "wechat"))
        db.commit()
        db.close()


def test_wechat_not_configured_rejected():
    """未配置微信凭证 → 回调拒绝（503）"""
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    params = {"msg_signature": "x", "timestamp": "1", "nonce": "2", "echostr": "y"}
    r = client.get("/api/v1/wechat/callback", params=params)
    assert r.status_code == 503


def test_wechat_delete_requires_auth():
    """审查 CRITICAL 修复：wechat/delete 未鉴权 → 匿名请求必须 401"""
    from app.main import app
    from fastapi.testclient import TestClient

    client = TestClient(app)
    r = client.post("/api/v1/wechat/delete", data={"msg_id": "1234567890"})
    assert r.status_code == 401
    assert r.json()["code"] == "AUTH_005"
