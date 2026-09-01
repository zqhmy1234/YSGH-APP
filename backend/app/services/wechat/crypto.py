"""企微回调加解密（官方协议 path/90931：AES-256-CBC + PKCS7）

明文结构：random16字节 + msg_len(4字节网络序) + msg(UTF-8) + receiveid
AESKey = base64_decode(EncodingAESKey)；IV = AESKey 前 16 字节
加密输出：base64(AES-CBC(明文)) —— 与官方 SDK 语义互操作

官方测试向量（path/90931）：
  Token=QDG6eK / EncodingAESKey=jWmYm7qr5nMoAUwZRjGtBxmz3KA1tkAj3ykkR6q2B2C / corpid=wx5823bf96d3bd56c7
"""
from __future__ import annotations

import base64
import os
import struct

from cryptography.hazmat.primitives import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def aes_key_from(encoding_aes_key: str) -> bytes:
    """43 位 EncodingAESKey → 32 字节 AES key（base64 补齐）"""
    return base64.b64decode(encoding_aes_key + "=")


def encrypt(msg: str, encoding_aes_key: str, receive_id: str) -> str:
    """明文消息 → 加密 base64 串（官方协议结构）"""
    key = aes_key_from(encoding_aes_key)
    iv = key[:16]
    msg_bytes = msg.encode("utf-8")
    raw = os.urandom(16) + struct.pack(">I", len(msg_bytes)) + msg_bytes + receive_id.encode("utf-8")
    padder = padding.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    cipher = encryptor.update(padded) + encryptor.finalize()
    return base64.b64encode(cipher).decode("ascii")


def decrypt(encrypted: str, encoding_aes_key: str) -> tuple[str, str]:
    """加密 base64 串 → (msg 明文, receive_id)；结构不符抛 ValueError"""
    key = aes_key_from(encoding_aes_key)
    iv = key[:16]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    raw = decryptor.update(base64.b64decode(encrypted)) + decryptor.finalize()
    unpadder = padding.PKCS7(128).unpadder()
    raw = unpadder.update(raw) + unpadder.finalize()
    if len(raw) < 20:
        raise ValueError("密文过短，解密结构非法")
    msg_len = struct.unpack(">I", raw[16:20])[0]
    msg = raw[20 : 20 + msg_len].decode("utf-8")
    receive_id = raw[20 + msg_len :].decode("utf-8")
    return msg, receive_id
