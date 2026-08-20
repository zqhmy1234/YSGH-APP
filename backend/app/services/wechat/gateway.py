"""企微回调网关（协议 path/90930+90931：URL 验证 + 收包处理）

- verify_url：GET 验证 URL——验签后解密 echostr，原样返回明文（企微据此确认回调 URL 归属）
- handle_message：POST 收消息——验签 → 解密 → 解析明文 XML → 消息 dict
- 消息类型：text / image / voice / event（本项目只收：text/image/voice 入库，其余忽略）

安全：验签失败必须拒绝（防伪造回调）；解密结构非法必须拒绝。
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET

from app.services.wechat.crypto import decrypt
from app.services.wechat.signature import verify

logger = logging.getLogger("yishu.wechat")


def verify_url(
    token: str, aes_key: str, corpid: str, msg_signature: str, timestamp: str, nonce: str, echostr: str
) -> str:
    """URL 验证：返回解密后的 echostr 明文（调用方原样返回给企微）"""
    if not verify(token, timestamp, nonce, echostr, msg_signature):
        raise ValueError("URL 验证签名不匹配")
    msg, receive_id = decrypt(echostr, aes_key)
    if receive_id != corpid:
        raise ValueError(f"receiveid 不匹配: {receive_id} != {corpid}")
    return msg


def handle_message(
    token: str,
    aes_key: str,
    corpid: str,
    msg_signature: str,
    timestamp: str,
    nonce: str,
    body: str,
) -> dict | None:
    """收包：验签+解密+解析 → 消息 dict；非支持类型返回 None"""
    # body 为 <xml><Encrypt>...</Encrypt></xml>
    root = ET.fromstring(body)
    encrypt_el = root.find("Encrypt")
    if encrypt_el is None or not encrypt_el.text:
        raise ValueError("回调缺少 Encrypt 字段")
    encrypt = encrypt_el.text.strip()

    if not verify(token, timestamp, nonce, encrypt, msg_signature):
        raise ValueError("回调签名不匹配")
    plain, receive_id = decrypt(encrypt, aes_key)
    if receive_id != corpid:
        raise ValueError(f"receiveid 不匹配: {receive_id} != {corpid}")

    return parse_message_xml(plain)


def parse_message_xml(plain: str) -> dict | None:
    """明文 XML → 消息 dict（text/image/voice 支持；event/其他返回 None）"""
    root = ET.fromstring(plain)

    def _txt(tag: str) -> str | None:
        el = root.find(tag)
        return el.text if el is not None and el.text else None

    msg_type = _txt("MsgType")
    if msg_type not in ("text", "image", "voice"):
        logger.info("忽略非内容消息类型: %s", msg_type)
        return None

    msg = {
        "msg_type": msg_type,
        "msg_id": _txt("MsgId"),
        "from_user": _txt("FromUserName"),
        "to_user": _txt("ToUserName"),
        "create_time": _txt("CreateTime"),
        "agent_id": _txt("AgentID"),
    }
    if msg_type == "text":
        msg["content"] = _txt("Content")
    elif msg_type == "image":
        msg["pic_url"] = _txt("PicUrl")
        msg["media_id"] = _txt("MediaId")
    elif msg_type == "voice":
        msg["media_id"] = _txt("MediaId")
        msg["format"] = _txt("Format")
    return msg
