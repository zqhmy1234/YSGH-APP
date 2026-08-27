"""微信域依赖端口（R1#9 依赖反转：services/wechat 只依赖端口契约，具体实现可替换）

端口 = 调用方所需的最小契约（Protocol）：
  - SignaturePort：企微回调验签（SHA1 + 时间窗新鲜度，防重放 G2）
  - CryptoPort：企微回调加解密（AES-256-CBC + PKCS7）
  - MediaGatewayPort：企微媒体下载（access_token + download_media）

具体实现（driver）：
  - app.services.wechat.signature / crypto —— 官方协议实现（duck-typed 满足端口）
  - app.services.wechat.service.download_media —— 企微媒体 HTTP 网关（默认驱动）

绑定位置：
  - gateway.py 模块级 `_signature`/`_crypto`（默认具体实现，可注入替身）
  - service.py `get_media_gateway()`（默认委托模块级 download_media，可注入替身）

纪律：wechat 域业务代码不得直接依赖 httpx/企微 URL/具体加解密算法实现——
统一经端口调用（测试可替换替身，生产配 key 后零切换）。
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SignaturePort(Protocol):
    """企微回调验签端口（官方协议：SHA1(sort(token,timestamp,nonce,encrypt))）"""

    def sign(self, token: str, timestamp: str, nonce: str, encrypt: str) -> str:
        """计算 msg_signature"""
        ...

    def verify(
        self,
        token: str,
        timestamp: str,
        nonce: str,
        encrypt: str,
        msg_signature: str,
        *,
        window_seconds: int = 300,
        now: float | None = None,
    ) -> bool:
        """验签 + 时间窗防重放（签名不匹配/时间戳越窗 → False）"""
        ...


@runtime_checkable
class CryptoPort(Protocol):
    """企微回调加解密端口（AES-256-CBC + PKCS7，明文结构 random16+len+msg+receiveid）"""

    def encrypt(self, msg: str, encoding_aes_key: str, receive_id: str) -> str:
        """明文消息 → 加密 base64 串"""
        ...

    def decrypt(self, encrypted: str, encoding_aes_key: str) -> tuple[str, str]:
        """加密 base64 串 → (msg 明文, receive_id)；结构非法抛 ValueError"""
        ...


@runtime_checkable
class MediaGatewayPort(Protocol):
    """企微媒体下载端口（image/voice → 字节；未配置/mock 模式返回 mock 字节）"""

    def get_access_token(self) -> str | None:
        """企业 access_token（进程内缓存 + 过期失效重取；未配置返回 None）"""
        ...

    def download_media(self, media_id: str, msg_type: str) -> bytes:
        """下载企微媒体字节（真实模式失败抛 RuntimeError；mock 返回可测试字节）"""
        ...
