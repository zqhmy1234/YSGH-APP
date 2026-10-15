"""`yishu/fetch.py` 的 SSRF 回归测试（2026-10-05 新增）。

## 为什么现在才补

`fetch.py` 是agent 唯一���出网面**此前没有任何测试**——而SSRF 恰恰是
"不报错、不告警、只在你被攻击时才发作"的缺陷类型：代码看起来有防护
（`_is_public_host` 逐条判私有网），实际却可被绕过。

## 本轮修的真实缺口：DNS 重绑定 TOCTOU

原实现是「校验域名 → 把**域名**交给 httpx」，而 httpx 建连时**会再解析一次**。
两次解析之间 DNS 可被改写（TTL 极短即可）：

    第 1 次解析（我们的校验） → 93.184.216.34   公网 ⇒ 通过
    第 2 次解析（httpx 建连）  → 127.0.0.1       内网 ⇒ 真的连到了本机

即"校验通过"和"实际连到的地方"不是同一个东西，逐跳复校验只能防住
「用重定向换host」，防不住「同一个 host 两次解析不同」。

正确形态是**只解析一次，把校验通过的那个 IP 钉成连接目标**，
让"校验的东西"就是"连的东西"（连 IP + 用 `sni_hostname` 按真实域名验证书）。

## 覆盖

1. 私网/环回/链路本地/metadata/CGNAT 一律解析为空（fail-closed）
2. 多 A 记录里**混进**私网 ⇒ 整体拒绝（不能"跳过坏的取其余"）
3. IP 固定后 URL / Host / SNI 三者一致（含非默认端口、IPv6 方括号）
4. **重绑定模拟**：两次解析给不同 IP，实际连接必须用第一次校验过的那个
5. 重定向到内网仍被逐跳拒绝（原有能力，防回归）
6. `file://` 等非 http(s) 协议仍被拒
"""
from __future__ import annotations

import socket

import pytest
from yishu import fetch as fetchmod
from yishu.fetch import FetchClient, _pin_to_ip, _resolve_public_ips


# ── 1 & 2. 地址判定 ──────────────────────────────────────────────────
@pytest.mark.parametrize(
    "host",
    [
        "localhost",  # 环回
        "127.0.0.1",
        "169.254.169.254",  # 云metadata，最经典的 SSRF 目标
        "10.20.28.21",  # 内网
        "100.64.1.1",  # CGNAT：ipaddress.is_private 对它返回 False，必须单列
        "0.0.0.0",  # noqa: S104 —— 这里不是绑定地址，而是**待拒绝的目标**；
        "::1",
    ],
)
def test_non_public_hosts_resolve_to_nothing(host: str):
    """非公网必须返回空元组（调用方据此拒绝），不是返回 False 了事。"""
    assert _resolve_public_ips(host) == ()


def test_mixed_public_and_private_records_fail_closed(monkeypatch):
    """A 记录里混私网 ⇒ **整体拒绝**。

    "跳过坏的取其余"看起来更友好，实际是把自己交给攻击者：
    配一个公网 IP + 一个 `127.0.0.1` 的域名就能过判定。
    """
    monkeypatch.setattr(
        fetchmod.socket,
        "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80)),
        ],
    )
    assert _resolve_public_ips("mixed.test") == ()


def test_dns_failure_resolves_to_nothing(monkeypatch):
    """解析失败必须 fail-closed（而不是"解析不了就当放行"）。"""

    def boom(*_a, **_k):
        raise OSError("NXDOMAIN")

    monkeypatch.setattr(fetchmod.socket, "getaddrinfo", boom)
    assert _resolve_public_ips("nope.test") == ()


# ── 3. IP 固定的三要素必须一致 ────────────────────────────────────────
@pytest.mark.parametrize(
    ("url", "ip", "want_url", "want_host"),
    [
        ("http://example.com/a?b=1", "93.184.216.34",
         "http://93.184.216.34/a?b=1", "example.com"),
        ("https://example.com/x", "93.184.216.34",
         "https://93.184.216.34/x", "example.com"),
        # 非默认端口必须同时出现在 URL 与 Host 头里
        ("http://example.com:8080/y", "93.184.216.34",
         "http://93.184.216.34:8080/y", "example.com:8080"),
        # 默认端口不该出现在 Host 头里（否则虚拟主机可能选错站点）
        ("https://example.com:443/z", "93.184.216.34",
         "https://93.184.216.34/z", "example.com"),
    ],
)
def test_pin_keeps_url_host_and_sni_consistent(url, ip, want_url, want_host):
    """连IP 之后仍要"看起来像"在访问原域名，否则虚拟主机与证书校验都会坏。"""
    pinned, headers, ext = _pin_to_ip(url, ip)
    assert pinned == want_url
    assert headers["Host"] == want_host
    # sni_hostname 是 httpcore 的官方扩展点：TLS 仍按真实域名校验证书
    assert ext["sni_hostname"] == "example.com"


def test_pin_brackets_ipv6_literal():
    """IPv6 字面量在 URL 里必须带方括号，否则整条 URL 解析失败。"""
    pinned, headers, ext = _pin_to_ip("http://example.com/", "2606:2800:220:1::1")
    assert pinned == "http://[2606:2800:220:1::1]/"
    assert headers["Host"] == "example.com"
    assert ext["sni_hostname"] == "example.com"


# ── 4. DNS 重绑定（本次修复的核心） ──────────────────────────────────
class _FakeResponse:
    """最小可用的 httpx.Response 替身。"""

    def __init__(self, url: str, status: int = 200,
                 headers: dict | None = None, body: bytes = b"<html><body>ok</body></html>"):
        self.url = url
        self.status_code = status
        self.headers = headers or {"content-type": "text/html"}
        self._body = body

    def iter_bytes(self):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _capture_stream(monkeypatch) -> dict:
    """替换 `httpx.Client.stream`，记录实际发出的 URL / 头 / 扩展。"""
    seen: dict = {}

    def fake_stream(_self, method, url, **kwargs):
        seen.setdefault("requests", []).append(
            {"method": method, "url": url,
             "headers": kwargs.get("headers"), "ext": kwargs.get("extensions")}
        )
        return _FakeResponse(url)

    monkeypatch.setattr(fetchmod.httpx.Client, "stream", fake_stream)
    return seen


def test_dns_rebinding_cannot_swap_connection_target(monkeypatch):
    """**本次修复的核心断言**。

    同一个 host：第 1 次解析给公网（我们的校验看到它），第 2 次给环回
    （攻击者的 rebinding）。实际连接必须落在**第一次校验过的那个 IP** 上——
    也就是"校验的东西 = 连的东西"，TOCTOU 被消除。
    """
    calls = {"n": 0}

    def fake_gai(_host, _port, *_a, **_k):
        calls["n"] += 1
        ip = "93.184.216.34" if calls["n"] == 1 else "127.0.0.1"
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 80))]

    monkeypatch.setattr(fetchmod.socket, "getaddrinfo", fake_gai)
    seen = _capture_stream(monkeypatch)

    result = FetchClient().fetch("http://rebind.test/page")

    assert result.status_code == 0, result.status_message
    first = seen["requests"][0]
    assert "93.184.216.34" in first["url"], "应连第一次校验过的公网 IP"
    assert "127.0.0.1" not in first["url"], "绝不能连到 rebinding 换上去的内网 IP"
    assert first["headers"]["Host"] == "rebind.test"
    assert first["ext"]["sni_hostname"] == "rebind.test"
    # 关键：整个过程只解析过一次（第二次 DNS 根本没机会发生）
    assert calls["n"] == 1, f"发生了重复解析（{calls['n']} 次），TOCTOU 窗口仍在"


# ── 5 & 6. 重定向与协议白名单（原有能力，防回归） ──────────────────────
def test_redirect_to_private_host_is_still_blocked(monkeypatch):
    """公网 → 302 → 内网：逐跳复校验必须拦住（防回归，2026-10-04 已修）。"""
    monkeypatch.setattr(
        fetchmod.socket, "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    )

    def fake_stream(_self, _method, url, **_kw):
        return _FakeResponse(url, status=302,
                             headers={"location": "http://127.0.0.1:8300/api/memories?user_id=x"})

    monkeypatch.setattr(fetchmod.httpx.Client, "stream", fake_stream)
    result = FetchClient().fetch("http://evil.test/r")
    assert result.status_code == 403
    assert "重定向" in result.status_message


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://x/y", "gopher://x"])
def test_non_http_schemes_are_rejected(url: str):
    """`urllib` 默认 opener 含 FileHandler；这里只允许 http/https。"""
    result = FetchClient().fetch(url)
    assert result.status_code == 400


def test_oversized_body_is_aborted_mid_stream(monkeypatch):
    """2MB 上限必须**边读边判**，不能等读完再截断（否则形同虚设）。"""
    monkeypatch.setattr(
        fetchmod.socket, "getaddrinfo",
        lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80))],
    )

    class _Huge(_FakeResponse):
        def iter_bytes(self):
            chunk = b"x" * (256 * 1024)
            for _ in range(16):  # 4MB > 2MB 上限
                yield chunk

    monkeypatch.setattr(fetchmod.httpx.Client, "stream",
                        lambda _s, _m, url, **_k: _Huge(url))
    result = FetchClient().fetch("http://big.test/")
    assert result.status_code == 413
    assert "上限" in result.status_message


def test_multiple_public_ips_all_returned_for_fallback(monkeypatch):
    """多 A 记录要全部返回：第一个连不通时能回退，否则正常双栈站点会被误判为抓不到。"""
    monkeypatch.setattr(
        fetchmod.socket, "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 80)),
        ],
    )
    assert _resolve_public_ips("multi.test") == ("93.184.216.34", "1.1.1.1")


# ── 7. 多 IP 回退必须**真的**生效（防止回退写成死代码） ────────────────
class _FailingConnect:
    """模拟"这一跳连不上"：错误在 `__enter__` 抛出，而不是在 `stream()` 调用时。

    这个区别是本用例存在的**全部理由**：`httpx.Client.stream()` 是
    `@contextmanager`，调用它只返回 `_GeneratorContextManager`、**不发起连接**；
    `ConnectError` / `ConnectTimeout` 是在 `__enter__` 才抛的。
    所以 `try: return client.stream(...) except ConnectError: <换下一个IP>`
    这种写法**永远抓不到异常**（第一轮就 return 了），回退会悄无声息地变成死代码。
    必须显式 `enter_context()` 才能把连接错误纳入 try —— 已实测确认。
    """

    def __enter__(self):
        raise fetchmod.httpx.ConnectError("connection refused (simulated)")

    def __exit__(self, *_exc) -> bool:  # pragma: no cover
        return False


def _two_public_ips(monkeypatch) -> None:
    monkeypatch.setattr(
        fetchmod.socket, "getaddrinfo",
        lambda *_a, **_k: [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 80)),
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("1.1.1.1", 80)),
        ],
    )


def test_multi_ip_fallback_actually_retries_next_ip(monkeypatch):
    """首个 IP 连不上时必须换下一个 IP 重试——否则双栈站点会被误判成抓不到。"""
    _two_public_ips(monkeypatch)
    attempted: list[str] = []

    def fake_stream(_self, _method, url, **_kw):
        attempted.append(url)
        if len(attempted) == 1:      # 第一个 IP 连不上
            return _FailingConnect()
        return _FakeResponse(url)    # 第二个 IP 通

    monkeypatch.setattr(fetchmod.httpx.Client, "stream", fake_stream)

    result = FetchClient().fetch("http://multi-ip.test/page")

    assert result.status_code == 0, result.status_message
    assert len(attempted) >= 2, f"没有回退到第二个 IP（只试了 {attempted}）"
    assert "93.184.216.34" in attempted[0]
    assert "1.1.1.1" in attempted[1]


def test_multi_ip_fallback_still_pins_host_and_sni(monkeypatch):
    """回退换 IP 时，Host 头与 SNI 仍必须是**原域名**（不能变成 IP 字面量）。"""
    _two_public_ips(monkeypatch)
    seen: list[dict] = []

    def fake_stream(_self, _method, url, **kw):
        seen.append({"url": url, "headers": kw.get("headers"), "ext": kw.get("extensions")})
        return _FailingConnect() if len(seen) == 1 else _FakeResponse(url)

    monkeypatch.setattr(fetchmod.httpx.Client, "stream", fake_stream)

    FetchClient().fetch("http://multi-ip.test/page")

    for call in seen:
        assert call["headers"]["Host"] == "multi-ip.test", "回退时 Host 头丢了"
        assert call["ext"]["sni_hostname"] == "multi-ip.test", "回退时 SNI 丢了"


def test_all_ips_failing_returns_502(monkeypatch):
    """所有候选 IP 都连不上 ⇒ 502（如实报网络错误，不静默返回空内容）。"""
    _two_public_ips(monkeypatch)
    monkeypatch.setattr(fetchmod.httpx.Client, "stream",
                        lambda _s, _m, _u, **_k: _FailingConnect())
    result = FetchClient().fetch("http://dead.test/")
    assert result.status_code == 502
