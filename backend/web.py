"""Public-web fetching with SSRF guards. No LLM involved.

Known limitations:
- DNS rebinding: destinations are validated by resolving DNS before connecting, but the HTTP client resolves
  the name again when it connects. A hostile DNS server could answer differently the second time. Closing that
  gap needs connecting to the validated IP directly.
- Time limits are best effort. `fetch_total_timeout` is checked at the start of every hop and between body
  chunks; one blocked socket operation can overrun it by up to the connect/read timeout. A DNS lookup is bounded
  by `fetch_dns_timeout`, but the OS call itself cannot be cancelled: a timed-out lookup keeps running on an
  abandoned daemon thread until the OS returns, and holds one of MAX_INFLIGHT_LOOKUPS slots meanwhile.
- Environment proxies are ignored (`trust_env=False`); there is no reviewed proxy design, so fetching from
  behind a mandatory proxy is unsupported.
"""

import ipaddress
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlsplit

import httpx

from backend.config import Settings, get_settings

ALLOWED_PORTS = {80, 443}
TEXT_TYPES = {"text/html", "application/xhtml+xml", "text/plain"}

Resolver = Callable[[str, int], list[str]]

MAX_INFLIGHT_LOOKUPS = 8
_lookup_slots = threading.BoundedSemaphore(MAX_INFLIGHT_LOOKUPS)


class UnsafeURLError(ValueError):
    """The URL is syntactically invalid or points somewhere we refuse to fetch."""


class DNSTimeoutError(Exception):
    """Name resolution did not finish in time (not a verdict on the URL itself)."""


def bounded_resolve(resolver: Resolver, host: str, port: int, timeout: float) -> list[str]:
    """Run `resolver` on a daemon thread and give up after `timeout` seconds. The lookup itself cannot be
    cancelled; see the module docstring."""
    if timeout <= 0:
        raise DNSTimeoutError(f"no time left to resolve {host!r}")
    slots = _lookup_slots  # release the semaphore we acquired, even if the module attribute is replaced later
    if not slots.acquire(timeout=timeout):
        raise DNSTimeoutError(f"too many DNS lookups still in flight; could not resolve {host!r}")
    box: dict = {}

    def work():
        try:
            box["addrs"] = resolver(host, port)
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller's thread
            box["error"] = exc
        finally:
            slots.release()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        raise DNSTimeoutError(f"DNS lookup for {host!r} exceeded {timeout:g}s")
    if "error" in box:
        raise box["error"]
    return box["addrs"]


@dataclass
class FetchResult:
    ok: bool
    requested_url: str
    final_url: str
    outcome: str  # ok | blocked | timeout | connect_error | http_error | too_large |
    #               too_many_redirects | unsupported_content_type
    reason: str | None = None
    status_code: int | None = None
    content_type: str | None = None
    body: bytes = b""
    redirects: list[str] = field(default_factory=list)


def default_resolver(host: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError) as exc:  # UnicodeError: IDNA label too long/empty
        raise UnsafeURLError(f"cannot resolve host {host!r}: {exc}") from exc
    return sorted({info[4][0] for info in infos})


NAT64_PREFIX = ipaddress.ip_network("64:ff9b::/96")


def _is_public(addr: str) -> bool:
    ip = ipaddress.ip_address(addr.split("%")[0])
    if isinstance(ip, ipaddress.IPv6Address):
        # Addresses that embed an IPv4 address are only as public as that IPv4 address.
        embedded = ip.ipv4_mapped or ip.sixtofour
        if embedded is None and ip in NAT64_PREFIX:
            embedded = ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
        if ip.teredo is not None:
            return False  # Teredo tunnels: never a legitimate fetch target
        if embedded is not None:
            return _is_public(str(embedded))
    return ip.is_global and not ip.is_multicast


def validate_url(url: str, resolver: Resolver = default_resolver) -> None:
    """Raise UnsafeURLError unless the URL is http(s), credential-free, on an allowed port, and
    every address its host resolves to is a public one."""
    if any(c.isspace() or ord(c) < 32 or ord(c) == 127 for c in url):
        raise UnsafeURLError("URL contains whitespace or control characters")
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError as exc:
        raise UnsafeURLError(f"malformed URL: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise UnsafeURLError("only http and https URLs are allowed")
    if parts.username is not None or parts.password is not None:
        raise UnsafeURLError("URLs with embedded credentials are not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeURLError("URL has no host")
    port = port or (443 if parts.scheme == "https" else 80)
    if port not in ALLOWED_PORTS:
        raise UnsafeURLError(f"port {port} is not allowed (only 80 and 443)")
    if host.lower().rstrip(".") == "localhost" or host.lower().endswith(".localhost"):
        raise UnsafeURLError("localhost is not allowed")
    try:
        addrs = [str(ipaddress.ip_address(host))]  # IP literal
    except ValueError:
        addrs = resolver(host, port)
    if not addrs:
        raise UnsafeURLError(f"host {host!r} did not resolve")
    for addr in addrs:
        if not _is_public(addr):
            raise UnsafeURLError(f"host {host!r} resolves to non-public address {addr}")


class Fetcher:
    """Fetches one public page: manual redirects (each hop validated), size and time limits."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        transport: httpx.BaseTransport | None = None,
        resolver: Resolver = default_resolver,
    ):
        self.settings = settings or get_settings()
        self.transport = transport
        self.resolver = resolver

    def fetch(self, url: str) -> FetchResult:
        s = self.settings
        result = FetchResult(ok=False, requested_url=url, final_url=url, outcome="blocked")
        timeout = httpx.Timeout(s.fetch_read_timeout, connect=s.fetch_connect_timeout)
        deadline = time.monotonic() + s.fetch_total_timeout
        current = url
        with httpx.Client(
            transport=self.transport,
            timeout=timeout,
            follow_redirects=False,
            trust_env=False,  # never pick up HTTP(S)_PROXY or SSL_CERT_* from the environment
            headers={"User-Agent": s.fetch_user_agent, "Accept": "text/html,text/plain;q=0.9,*/*;q=0.1"},
        ) as client:
            for hop in range(s.fetch_max_redirects + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    result.final_url, result.outcome = current, "timeout"
                    result.reason = f"total fetch time exceeded {s.fetch_total_timeout}s before hop {hop + 1}"
                    return result
                lookup_budget = min(s.fetch_dns_timeout, remaining)
                try:
                    validate_url(current, lambda h, p, b=lookup_budget: bounded_resolve(self.resolver, h, p, b))
                except UnsafeURLError as exc:
                    result.final_url, result.outcome, result.reason = current, "blocked", str(exc)
                    return result
                except DNSTimeoutError as exc:
                    result.final_url, result.outcome, result.reason = current, "timeout", str(exc)
                    return result
                result.final_url = current
                try:
                    with client.stream("GET", current) as resp:
                        result.status_code = resp.status_code
                        if resp.is_redirect and "location" in resp.headers:
                            current = urljoin(current, resp.headers["location"])
                            result.redirects.append(current)
                            continue
                        return self._read(resp, result, deadline)
                except httpx.InvalidURL as exc:
                    result.outcome, result.reason = "blocked", f"invalid URL: {exc}"
                    return result
                except httpx.TimeoutException as exc:
                    result.outcome, result.reason = "timeout", f"{type(exc).__name__}: {exc}"
                    return result
                except httpx.HTTPError as exc:
                    result.outcome, result.reason = "connect_error", f"{type(exc).__name__}: {exc}"
                    return result
            result.outcome = "too_many_redirects"
            result.reason = f"more than {s.fetch_max_redirects} redirects"
            return result

    def _read(self, resp: httpx.Response, result: FetchResult, deadline: float) -> FetchResult:
        s = self.settings
        if resp.status_code != 200:
            result.outcome, result.reason = "http_error", f"HTTP {resp.status_code}"
            return result
        ctype = resp.headers.get("content-type", "").split(";")[0].strip().lower()
        result.content_type = ctype
        if ctype not in TEXT_TYPES:
            result.outcome = "unsupported_content_type"
            result.reason = f"content type {ctype or 'missing'!r} is not supported (HTML or plain text only)"
            return result
        declared = resp.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > s.fetch_max_bytes:
            result.outcome, result.reason = "too_large", f"declared size {declared} exceeds {s.fetch_max_bytes} bytes"
            return result
        chunks, size = [], 0
        for chunk in resp.iter_bytes():
            size += len(chunk)
            if size > s.fetch_max_bytes:
                result.outcome, result.reason = "too_large", f"body exceeds {s.fetch_max_bytes} bytes"
                return result
            if time.monotonic() > deadline:
                result.outcome, result.reason = "timeout", f"total fetch time exceeded {s.fetch_total_timeout}s"
                return result
            chunks.append(chunk)
        result.ok, result.outcome, result.body = True, "ok", b"".join(chunks)
        result.content_type = resp.headers.get("content-type", ctype)
        return result
