"""企微回调网关（协议 path/90930+90931：URL 验证 + 收包处理）

- verify_url：GET 验证 URL——验签后解密 echostr，原样返回明文（企微据此确认回调 URL 归属）
- handle_message：POST 收消息——验签 → 解密 → 解析明文 XML → 消息 dict
- 消息类型：text / image / voice / event（本项目只收：text/image/voice 入库，其余忽略）

安全：验签失败必须拒绝（防伪造回调）；解密结构非法必须拒绝。

R1#9 依赖反转：本模块只依赖端口契约（ports.SignaturePort / ports.CryptoPort），
默认绑定具体实现（signature/crypto 模块）；测试/扩展可替换 `_signature`/`_crypto`。
"""
from __future__ import annotations

import logging

# 安全（P1-1 · 2026-09-10 深扫）：企微回调 body 在验签前解析，ET 默认展开内部
# 实体 → 未认证 billion-laughs DoS 面。defusedxml 拦截实体膨胀（ET 兼容 API，
# 不改解析逻辑）；ElementTree 本就不解析外部实体，故无 XXE 外泄，仅补 DoS 防线。
import defusedxml.ElementTree as ET

from app.services.wechat import crypto as _crypto_impl
from app.services.wechat import ports
from app.services.wechat import signature as _signature_impl

logger = logging.getLogger("yishu.wechat")

# 端口绑定（默认具体实现；duck-typed 满足端口契约，可注入替身）
_signature: ports.SignaturePort = _signature_impl
_crypto: ports.CryptoPort = _crypto_impl

# 回调/明文 XML 包体上限（企微消息 XML 实测 <8KB，留 10 倍余量防大 body 内存吃满）
_MAX_XML_BYTES = 100_000


def _parse_xml(text: str):
    """安全解析（P1-1 · 2026-09-10 深扫）：
    - defusedxml 拦截内部实体膨胀（billion-laughs）→ 抛 DefusedXmlException(ValueError 子类)，
      api 层 `except ValueError → 403` 接得住；
    - 顺带堵既有缺口：ET.ParseError（畸形 XML）继承 SyntaxError 非 ValueError，
      原生实现下畸形 body 会漏过 `except ValueError` 变 500——统一转 ValueError。
    body 超 _MAX_XML_BYTES 直接拒（早于解析，防大 body DoS）。"""
    if len(text.encode("utf-8", "ignore")) > _MAX_XML_BYTES:
        raise ValueError("回调包体超长")
    try:
        return ET.fromstring(text)
    except ET.ParseError as exc:  # defusedxml 亦复用该 ParseError 名
        raise ValueError(f"XML 解析失败: {exc}") from exc


def verify_url(
    token: str, aes_key: str, corpid: str, msg_signature: str, timestamp: str, nonce: str, echostr: str
) -> str:
    """URL 验证：返回解密后的 echostr 明文（调用方原样返回给企微）"""
    if not _signature.verify(token, timestamp, nonce, echostr, msg_signature):
        raise ValueError("URL 验证签名不匹配")
    msg, receive_id = _crypto.decrypt(echostr, aes_key)
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
    root = _parse_xml(body)
    encrypt_el = root.find("Encrypt")
    if encrypt_el is None or not encrypt_el.text:
        raise ValueError("回调缺少 Encrypt 字段")
    encrypt = encrypt_el.text.strip()

    if not _signature.verify(token, timestamp, nonce, encrypt, msg_signature):
        raise ValueError("回调签名不匹配")
    plain, receive_id = _crypto.decrypt(encrypt, aes_key)
    if receive_id != corpid:
        raise ValueError(f"receiveid 不匹配: {receive_id} != {corpid}")

    return parse_message_xml(plain)


def parse_message_xml(plain: str) -> dict | None:
    """明文 XML → 消息 dict（text/image/voice 支持；event/其他返回 None）"""
    root = _parse_xml(plain)

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
