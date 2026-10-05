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
from contextlib import ExitStack
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger("yishu.agent.fetch")

_MAX_BYTES = 2 * 1024 * 1024
_TIMEOUT_S = 20.0
# 手动跟随重定向的最大跳数（2026-10-04 SSRF 加固）。浏览器上限通常 20，
# 这里取 5——正常站点 1~2 跳足够，5 跳以上多半是跳转农场/恶意链。
_MAX_REDIRECTS = 5
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


def _is_public_ip(ip: ipaddress._BaseAddress) -> bool:
    """单个 IP 是否可抓（公网且非保留段）。

    100.64.0.0/10（CGNAT / 部分云厂商内网段）必须单列：Python `ipaddress` 的
    `is_private` 对它返回 **False**（RFC 6598 是共享地址空间，不是私网），
    只判 is_private 会被绕过 —— 而云厂商的元数据/内部服务常落在这段。
    """
    if ip in ipaddress.ip_network("100.64.0.0/10"):
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _resolve_public_ips(host: str) -> tuple[str, ...]:
    """解析 `host` 并**只返回已判定为公网的 IP 列表**。

    ## 为什么要返回 IP 而不只是 True/False（2026-10-05，修 DNS 重绑定 TOCTOU）

    原`_is_public_host()` 只回答"这个域名看起来是公网吗"，然后把**域名**交给
    httpx —— httpx 在建连时**会再解析一次**。两次解析之间 DNS 可以被改写
    （DNS rebinding，TTL 极短即可）：第一次返回公网 IP 通过校验，
    第二次返回 `127.0.0.1` / `169.254.169.254` ⇒ 校验与实际连接的目标不一致，
    SSRF 成立。逐跳复校验只能防住"用重定向换 host"，防不住"同一个 host 两次解析不同"。

    所以正确形态是：**只解析一次，把校验通过的那个 IP 直接钉成连接目标**，
    让"校验的东西"就是"连的东西"。

    fail-closed：任一条目解析不出IP 或不是公网 ⇒ **整体拒绝**（返回空元组）。
    宽松地"跳过坏的、取其余"会让攻击者用多 A 记录掺私网地址绕过。
    """
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except OSError:
        return ()
    ips: list[str] = []
    for info in infos:
        raw = info[4][0]
        try:
            ip = ipaddress.ip_address(raw)
        except ValueError:
            return ()  # 判定不了 ⇒ 不放行
        if not _is_public_ip(ip):
            return ()
        if raw not in ips:
            ips.append(raw)
    return tuple(ips)


def _pin_to_ip(url: str, ip: str) -> tuple[str, dict[str, str], dict[str, str]]:
    """把 URL 的 host 换成 `ip`，同时保留 `Host` 头与 TLS SNI。

    返回 `(pinned_url, extra_headers, request_extensions)`。

    ## 为什么必须同时给Host 与 sni_hostname

    ·只换 URL 不给 Host：服务器看到的是 IP 形式的 Host，虚拟主机选错站点，
      且**证书校验会拿 IP 去比对** ⇒ 必然失败（不是安全问题，是功能直接坏）。
    · 只给 Host 不换 URL：等于没防住 —— httpx 仍然自己解析并连到第二次解析的结果。
    · `sni_hostname`（httpcore `request.extensions`）：让 TLS **仍按真实域名**
      校验证书与发送 SNI。这是 httpcore 官方支持的扩展点
      （`connection.py`: `server_hostname = sni_hostname or origin.host`），
      于是"连IP + 验域名"两件事同时成立。

    IPv6 字面量在 URL 里必须带方括号（`http://[::1]:80/`），否则解析失败。
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""
    literal = f"[{ip}]" if ":" in ip else ip
    default_port = 443 if parsed.scheme == "https" else 80
    netloc = literal if parsed.port in (None, default_port) else f"{literal}:{parsed.port}"
    pinned = parsed._replace(netloc=netloc).geturl()
    # Host 头必须带非默认端口，否则同一 IP 上的其他虚拟主机/端口会串
    host_header = host if parsed.port in (None, default_port) else f"{host}:{parsed.port}"
    return pinned, {"Host": host_header}, {"sni_hostname": host}


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

        # 2026-10-05 SSRF 闭环：解析**一次**、校验、**把该 IP 钉成连接目标**。
        # 早期实现是"校验域名 → 把域名交给 httpx"，而 httpx 建连时再解析一次，
        # 两次解析结果可以不同（DNS rebinding）⇒ 校验通过 ≠ 实际连到那里。
        ips = _resolve_public_ips(parsed.hostname)
        if not ips:
            # 不做静默放行：非公网地址直接拒绝（Agent 面向用户输入，必须防 SSRF）
            return FetchResult(
                status_code=403,
                status_message=(
                    f"拒绝抓取非公网地址（SSRF 防护）：{parsed.hostname}"
                ),
                url=url,
            )
        # 首个 IP 的钉法；其余候选 IP 由 `_open_pinned` 按同一规则推出。
        # 这里只取 headers/extensions —— Host 头与 SNI 只与**域名**有关，与选哪个 IP 无关。
        _pinned, pin_headers, pin_ext = _pin_to_ip(url, ips[0])
        connect_ips = ips

        # 2026-10-04 安全加固：**手动逐跳跟随重定向，每跳复校验 + 重新钉 IP**。
        # 原实现 `follow_redirects=True` 只校验初始 URL，于是：
        #     https://evil.example/r → 302 Location: http://127.0.0.1:8300/api/memories?user_id=…
        # httpx 会自动跟过去，把**内网响应体**回给 agent → 回给用户 = 完整 SSRF
        # （本机实测 agent 与后端同机，内网即数据库与记忆服务）。
        try:
            with httpx.Client(
                follow_redirects=False,  # ← 关键：不让库自动跳
                timeout=_TIMEOUT_S,
                headers={"User-Agent": _UA, "Accept-Language": "zh-CN,zh;q=0.9"},
            ) as client:
                # `src` 是**未钉 IP** 的真实 URL（拼重定向、算 Host 头要用它）；
                # `current` 是钉了 IP 的实际连接目标。两者必须一起推进。
                src = url
                current_headers = dict(pin_headers)
                current_ext = dict(pin_ext)
                raw = b""
                final_url = url
                status_code = 0
                content_type_header = ""
                for _hop in range(_MAX_REDIRECTS + 1):
                    # ⚠️ 2026-10-05 安全/可用性：原先是 `client.get(current)` —— httpx 会把
                    # **整个响应体先读进内存**，之后再用 `resp.content[:_MAX_BYTES]`
                    # 截断。那样 2MB 上限是**事后**生效，起不到保护作用：构造一个
                    # 500MB 响应就能把进程内存打爆（单次工具调用即可）。
                    # 改为流式读取 + 边读边计数，超限立刻中断连接。
                    stack, candidate = self._open_pinned(
                        client, src, current_headers, connect_ips, current_ext
                    )
                    with stack:
                        if candidate.status_code in (301, 302, 303, 307, 308):
                            location = candidate.headers.get("location", "")
                            nxt = urljoin(src, location) if location else ""
                            nxt_parsed = urlparse(nxt) if nxt else None
                            # 每一跳都重新校验：scheme + 公网地址
                            if (
                                not nxt
                                or nxt_parsed is None
                                or nxt_parsed.scheme not in ("http", "https")
                                or not nxt_parsed.hostname
                            ):
                                return FetchResult(
                                    status_code=403,
                                    status_message="重定向目标协议非法（SSRF 防护）",
                                    url=url,
                                )
                            hop_ips = _resolve_public_ips(nxt_parsed.hostname)
                            if not hop_ips:
                                return FetchResult(
                                    status_code=403,
                                    status_message=(
                                        "拒绝跟随重定向到非公网地址（SSRF 防护）："
                                        f"{nxt_parsed.hostname}"
                                    ),
                                    url=url,
                                )
                            # 换 host ⇒ 重新解析、重新校验、重新钉 IP、重算 Host 头
                            src = nxt
                            _pinned, current_headers, current_ext = _pin_to_ip(nxt, hop_ips[0])
                            connect_ips = hop_ips
                            continue
                        # 非重定向：流式收正文，超限即断
                        resp = candidate
                        chunks: list[bytes] = []
                        total = 0
                        for chunk in candidate.iter_bytes():
                            total += len(chunk)
                            if total > _MAX_BYTES:
                                return FetchResult(
                                    status_code=413,
                                    status_message=f"响应体超过 {_MAX_BYTES} 字节上限（已中断）",
                                    url=url,
                                )
                            chunks.append(chunk)
                        raw = b"".join(chunks)
                        final_url = str(candidate.url)
                        status_code = candidate.status_code
                        content_type_header = candidate.headers.get("content-type", "")
                        break
                else:
                    return FetchResult(
                        status_code=403,
                        status_message=f"重定向跳数超过 {_MAX_REDIRECTS}（SSRF 防护）",
                        url=url,
                    )
                if raw is None:  # pragma: no cover —— 循环体必赋值，防御性兜底
                    return FetchResult(status_code=502, status_message="未取得响应体", url=url)
        except httpx.TimeoutException:
            return FetchResult(status_code=504, status_message="抓取超时", url=url)
        except httpx.HTTPError as exc:
            return FetchResult(status_code=502, status_message=f"网络错误：{exc}", url=url)

        if status_code >= 400:
            return FetchResult(status_code=status_code, status_message=f"HTTP {status_code}", url=url)
        ctype = (content_type_header or "").split(";")[0].strip().lower()
        filetype = ctype or None

        if ctype and not (ctype.startswith("text/") or ctype in ("application/xhtml+xml",)):
            return FetchResult(
                status_code=415,
                status_message=f"暂不支持的内容类型：{ctype}",
                url=final_url,
                filetype=filetype,
            )

        return self._extract(final_url, raw, filetype)

    def _open_pinned(
        self,
        client: httpx.Client,
        unpinned_url: str,
        headers: dict[str, str],
        ips: tuple[str, ...],
        extensions: dict[str, str],
    ) -> tuple[ExitStack, Any]:
        """按固定 IP 打开响应流，首个 IP 连不上时依次回退。

        返回 `(ExitStack, response)`；调用方必须 `with stack:` 使用，
        以保证所有已进入的上下文都正确收尾。

        ## 多 IP 回退的必要性

        域名常返回多个 A/AAAA 记录，第一个可能已下线或被防火墙挡住。
        不回退的话，正常的双栈站点会被误判成"抓不到"。
        **只在这一层放宽** —— 每个候选 IP 都出自 `_resolve_public_ips` 的白名单，
        不会出现"为了连通性而绕过 SSRF 判定"。

        ## 为什么必须用 `ExitStack`（而不是简单 try/except 包住 stream）

        `httpx.Client.stream()` 是 `@contextmanager`，调用它**只返回一个
        `_GeneratorContextManager`，不会发起连接**——实测 `client.stream(...)`
        正常返回，而 `ConnectTimeout` 是在 `__enter__` 才抛的。
        所以 `try: return client.stream(...) except ConnectError:` 的回退分支
        **永远抓不到异常**（第一轮就 return 了），回退成了死代码。
        必须显式 `enter_context()` 才能把连接错误纳入 try。
        """
        candidates: list[str] = []
        for ip in ips:
            pinned, _headers, _ext = _pin_to_ip(unpinned_url, ip)
            if pinned not in candidates:
                candidates.append(pinned)

        stack = ExitStack()
        last_exc: Exception | None = None
        for candidate_url in candidates:
            try:
                # `headers` 里带着真实域名的 `Host`（与IP 无关，故所有候选共用）：
                # 不带它的话 Host 会变成 IP 字面量，虚拟主机会选错站点。
                response = stack.enter_context(
                    client.stream(
                        "GET", candidate_url, headers=headers, extensions=extensions
                    )
                )
            except (httpx.ConnectError, httpx.ConnectTimeout) as exc:
                last_exc = exc
                continue
            return stack, response
        stack.close()
        if last_exc is not None:
            raise last_exc
        raise httpx.ConnectError("所有候选 IP 均无法连接")

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
