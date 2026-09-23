"""网页内容抓取（替代 `coze_coding_dev_sdk.fetch.FetchClient`）。

**结果契约照上游逐字段对齐**（依据 `tools/url_fetch_tools.py:56-96` 的实际读取）：
    response.status_code      int，**0 = 成功**（非 0 即失败，上游据此分支）
    response.status_message   str
    response.content          list[item]，item.type ∈ {text, image, link}
        text  → item.text
        image → item.image.{image_url, display_url, width, height}
        link  → item.url
    response.url / title / publish_time / filetype

实现：httpx 取页 + BeautifulSoup(lxml) 抽取正文/图片/链接。
安全（上游 SDK 由平台代理，本地实现必须自己扛）：
  · **SSRF 防护**：解析目标 IP，拒私有/环回/链路本地/保留段（用户可让 Agent 抓任意链接）
  · 体积上限 2MB、超时 20s、只接受 text/html 与 text/plain
诚实：抓不到就是抓不到——返回非 0 status_code + 说明，不编造内容。
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("yishu.agent.fetch")

_MAX_BYTES = 2 * 1024 * 1024
_TIMEOUT_S = 20.0
_UA = (
    "Mozilla/5.0 (Linux; Android 13) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0 Mobile Safari/537.36 yishu-agent/1.0"
)
_STRIP_TAGS = ("script", "style", "noscript", "iframe", "svg", "nav", "footer", "aside", "form")


@dataclass
class FetchedImage:
    image_url: str
    display_url: str
    width: int | None = None
    height: int | None = None


@dataclass
class ContentItem:
    type: str  # text | image | link
    text: str | None = None
    image: FetchedImage | None = None
    url: str | None = None


@dataclass
class FetchResult:
    status_code: int = 0
    status_message: str = "ok"
    url: str = ""
    title: str = ""
    publish_time: str | None = None
    filetype: str | None = None
    content: list[ContentItem] = field(default_factory=list)


def _is_public_host(host: str) -> bool:
    """SSRF 防护：目标必须是公网地址（域名解析后逐 IP 判定）。"""
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return False
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return False
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            return False
    return True


def _absolutize(base: str, url: str) -> str:
    try:
        return urljoin(base, url)
    except ValueError:
        return url


class FetchClient:
    """与上游同名的抓取客户端（`ctx` 参数保留以兼容调用点，本实现不使用）。"""

    def __init__(self, ctx: Any = None) -> None:
        self.ctx = ctx

    def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return FetchResult(status_code=400, status_message=f"仅支持 http/https 链接：{url}", url=url)
        if not _is_public_host(parsed.hostname):
            # 不做静默放行：内网地址直接拒绝（Agent 面向用户输入，必须防 SSRF）
            return FetchResult(
                status_code=403,
                status_message=f"拒绝抓取非公网地址（SSRF 防护）：{parsed.hostname}",
                url=url,
            )

        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=_TIMEOUT_S,
                headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            ) as client:
                resp = client.get(url)
        except httpx.TimeoutException:
            return FetchResult(status_code=504, status_message="抓取超时", url=url)
        except httpx.HTTPError as exc:
            return FetchResult(status_code=502, status_message=f"网络错误：{exc}", url=url)

        if resp.status_code >= 400:
            return FetchResult(status_code=resp.status_code, status_message=f"HTTP {resp.status_code}", url=url)
        raw = resp.content[:_MAX_BYTES]
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        filetype = ctype or None

        if ctype and not (ctype.startswith("text/") or ctype in ("application/xhtml+xml",)):
            return FetchResult(
                status_code=415,
                status_message=f"暂不支持的内容类型：{ctype}",
                url=str(resp.url),
                filetype=filetype,
            )

        return self._extract(str(resp.url), raw, filetype)

    def _extract(self, url: str, raw: bytes, filetype: str | None) -> FetchResult:
        """从 HTML 抽取标题/正文/图片/链接（bs4 + lxml）。"""
        try:
            from bs4 import BeautifulSoup
        except ImportError:  # 明确失败，不静默返回空内容
            return FetchResult(
                status_code=500,
                status_message="缺少 beautifulsoup4/lxml —— 无法抽取正文（pip install beautifulsoup4 lxml）",
                url=url,
                filetype=filetype,
            )

        html = raw.decode("utf-8", errors="replace")
        soup = BeautifulSoup(html, "lxml")

        title = ""
        og_title = soup.find("meta", attrs={"property": "og:title"})
        if og_title and og_title.get("content"):
            title = str(og_title["content"]).strip()
        elif soup.title and soup.title.string:
            title = soup.title.string.strip()

        publish_time = None
        for key in ("article:published_time", "og:published_time"):
            meta = soup.find("meta", attrs={"property": key})
            if meta and meta.get("content"):
                publish_time = str(meta["content"]).strip()
                break

        images: list[FetchedImage] = []
        for img in soup.find_all("img"):
            src = img.get("src") or img.get("data-src")
            if not src:
                continue
            abs_url = _absolutize(url, str(src))
            if not abs_url.startswith("http"):
                continue
            width = _int_or_none(img.get("width"))
            height = _int_or_none(img.get("height"))
            images.append(FetchedImage(image_url=abs_url, display_url=abs_url, width=width, height=height))
            if len(images) >= 50:
                break

        links: list[str] = []
        for a in soup.find_all("a"):
            href = a.get("href")
            if not href:
                continue
            abs_url = _absolutize(url, str(href))
            if abs_url.startswith("http") and abs_url not in links:
                links.append(abs_url)
            if len(links) >= 100:
                break

        for tag in soup(_STRIP_TAGS):
            tag.decompose()
        body = soup.find("article") or soup.find("main") or soup.body or soup
        text = body.get_text("\n", strip=True) if hasattr(body, "get_text") else ""
        lines = [ln.strip() for ln in text.splitlines()]
        cleaned = "\n".join(ln for ln in lines if ln)

        content: list[ContentItem] = []
        if cleaned:
            content.append(ContentItem(type="text", text=cleaned))
        content.extend(ContentItem(type="image", image=img) for img in images)
        content.extend(ContentItem(type="link", url=link) for link in links)

        return FetchResult(
            status_code=0,
            status_message="ok",
            url=url,
            title=title,
            publish_time=publish_time,
            filetype=filetype,
            content=content,
        )


def _int_or_none(value: Any) -> int | None:
    try:
        return int(str(value).replace("px", "").strip())
    except (TypeError, ValueError):
        return None


__all__ = ["ContentItem", "FetchClient", "FetchedImage", "FetchResult"]
