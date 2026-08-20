"""企微回调沙箱模拟器（协议 1:1，官方测试凭证）

与真实企微完全同协议（AES-256-CBC 加解密 + SHA1 签名 + XML 报文），
仅凭证为官方文档测试值（path/90931）：
  Token=*** / EncodingAESKey=jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C / corpid=wx5823bf96d3bd56c7

用法：
  python scripts/wecom_sandbox.py verify <base_url>       # 模拟 URL 验证 GET
  python scripts/wecom_sandbox.py send <base_url> text "明天记得取快递"  # 模拟文本消息回调
  python scripts/wecom_sandbox.py send <base_url> image <media_id>
  python scripts/wecom_sandbox.py send <base_url> voice <media_id>
"""
from __future__ import annotations

import argparse
import sys
import time
import uuid

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = __import__("pathlib").Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.wechat.crypto import encrypt  # noqa: E402
from app.services.wechat.signature import sign  # noqa: E402

TOKEN = "QDG6eK"
AES_KEY = "jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C"
CORP_ID = "wx5823bf96d3bd56c7"


def _xml_wrap(encrypted: str) -> str:
    return f"<xml><Encrypt><![CDATA[{encrypted}]]></Encrypt></xml>"


def _text_message_xml(content: str, msg_id: str) -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[toUser]]></ToUserName>"
        "<FromUserName><![CDATA[fromUser]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[text]]></MsgType>"
        f"<Content><![CDATA[{content}]]></Content>"
        f"<MsgId>{msg_id}</MsgId>"
        "<AgentID>1</AgentID>"
        "</xml>"
    )


def _image_message_xml(media_id: str, msg_id: str) -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[toUser]]></ToUserName>"
        "<FromUserName><![CDATA[fromUser]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[image]]></MsgType>"
        "<PicUrl><![CDATA[http://mmbiz.qpic.cn/x]]></PicUrl>"
        f"<MediaId><![CDATA[{media_id}]]></MediaId>"
        f"<MsgId>{msg_id}</MsgId>"
        "<AgentID>1</AgentID>"
        "</xml>"
    )


def _voice_message_xml(media_id: str, msg_id: str) -> str:
    return (
        "<xml>"
        "<ToUserName><![CDATA[toUser]]></ToUserName>"
        "<FromUserName><![CDATA[fromUser]]></FromUserName>"
        f"<CreateTime>{int(time.time())}</CreateTime>"
        "<MsgType><![CDATA[voice]]></MsgType>"
        f"<MediaId><![CDATA[{media_id}]]></MediaId>"
        "<Format><![CDATA[amr]]></Format>"
        f"<MsgId>{msg_id}</MsgId>"
        "<AgentID>1</AgentID>"
        "</xml>"
    )


def build_callback_request(msg_type: str, payload: str) -> dict:
    """构造协议级回调请求（encrypt + 签名 + 参数）——与企微发来的一致"""
    msg_id = str(uuid.uuid4().int % 10**15)
    if msg_type == "text":
        plain = _text_message_xml(payload, msg_id)
    elif msg_type == "image":
        plain = _image_message_xml(payload, msg_id)
    elif msg_type == "voice":
        plain = _voice_message_xml(payload, msg_id)
    else:
        raise ValueError(f"不支持的消息类型: {msg_type}")

    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4().int % 10**10)
    encrypted = encrypt(plain, AES_KEY, CORP_ID)
    msg_signature = sign(TOKEN, timestamp, nonce, encrypted)
    return {
        "body": _xml_wrap(encrypted),
        "params": {"msg_signature": msg_signature, "timestamp": timestamp, "nonce": nonce},
        "msg_id": msg_id,
    }


def build_verify_request(echostr_plain: str) -> dict:
    """构造协议级 URL 验证请求"""
    timestamp = str(int(time.time()))
    nonce = str(uuid.uuid4().int % 10**10)
    encrypted = encrypt(echostr_plain, AES_KEY, CORP_ID)
    msg_signature = sign(TOKEN, timestamp, nonce, encrypted)
    return {
        "params": {
            "msg_signature": msg_signature,
            "timestamp": timestamp,
            "nonce": nonce,
            "echostr": encrypted,
        },
        "expected_plain": echostr_plain,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_verify = sub.add_parser("verify", help="模拟 URL 验证")
    p_verify.add_argument("base_url")

    p_send = sub.add_parser("send", help="模拟消息回调")
    p_send.add_argument("base_url")
    p_send.add_argument("msg_type", choices=["text", "image", "voice"])
    p_send.add_argument("payload")

    args = parser.parse_args()
    import httpx

    if args.cmd == "verify":
        req = build_verify_request("1616140317555161061")
        r = httpx.get(f"{args.base_url}/api/v1/wechat/callback", params=req["params"], timeout=10)
        ok = r.text == req["expected_plain"]
        print(f"URL 验证: HTTP {r.status_code} 期望={req['expected_plain']} 返回={r.text!r} → {'✅' if ok else '❌'}")
        sys.exit(0 if ok else 1)
    elif args.cmd == "send":
        req = build_callback_request(args.msg_type, args.payload)
        r = httpx.post(
            f"{args.base_url}/api/v1/wechat/callback",
            params=req["params"], content=req["body"], timeout=10,
        )
        ok = r.text == "success"
        print(
            f"回调({args.msg_type} msg_id={req['msg_id']}): HTTP {r.status_code} "
            f"返回={r.text!r} → {'✅' if ok else '❌'}"
        )
        sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
