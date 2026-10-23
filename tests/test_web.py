import httpx
import pytest

from backend.config import Settings
from backend.web import Fetcher, UnsafeURLError, validate_url
from tests.conftest import fake_resolver


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.test/x",
        "file:///etc/passwd",
        "https://user:pw@example.test/",
        "http://localhost/",
        "http://app.localhost/",
        "http://127.0.0.1/",
        "http://[::1]/",
        "http://[::ffff:127.0.0.1]/",
        "http://10.1.2.3/",
        "http://172.16.0.1/",
        "http://192.168.0.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://0.0.0.0/",
        "http://100.64.0.1/",
        "http://internal.test/",
        "http://loopback.test/",
        "http://mixed.test/",  # one public and one private answer: still refused
        "http://example.test:8080/",
        "http:///nohost",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(UnsafeURLError):
        validate_url(url, fake_resolver)


def test_public_urls_accepted():
    validate_url("https://example.test/page", fake_resolver)
    validate_url("http://93.184.216.34/", fake_resolver)


def test_unresolvable_host_rejected():
    def nxdomain(host, port):
        raise UnsafeURLError("cannot resolve")

    with pytest.raises(UnsafeURLError):
        validate_url("https://nowhere.test/", nxdomain)


def test_redirect_to_private_address_is_blocked_without_requesting_it(web, fetcher):
    web.pages["https://example.test/r"] = (302, {"location": "http://internal.test/admin"}, b"")
    web.pages["http://internal.test/admin"] = (200, {"content-type": "text/html"}, b"<p>secret</p>")
    result = fetcher.fetch("https://example.test/r")
    assert result.outcome == "blocked" and "10.0.0.5" in result.reason
    assert web.requests == ["https://example.test/r"]  # the unsafe hop was never requested


def test_safe_relative_redirect_is_followed(web, fetcher):
    web.pages["https://example.test/old"] = (301, {"location": "/new"}, b"")
    web.html("https://example.test/new", "<p>Hello</p>")
    result = fetcher.fetch("https://example.test/old")
    assert result.ok and result.final_url == "https://example.test/new"
    assert result.redirects == ["https://example.test/new"]


def test_redirect_limit(web, settings):
    web.pages["https://example.test/loop"] = (302, {"location": "/loop"}, b"")
    f = Fetcher(settings.model_copy(update={"fetch_max_redirects": 3}),
                transport=httpx.MockTransport(web.handler), resolver=fake_resolver)
    result = f.fetch("https://example.test/loop")
    assert result.outcome == "too_many_redirects" and len(web.requests) == 4


def test_response_size_limit_streamed(web, fetcher):
    web.html("https://example.test/big", "<p>" + "a" * 60_000 + "</p>")  # limit is 50_000
    assert fetcher.fetch("https://example.test/big").outcome == "too_large"


def test_declared_size_limit(web, fetcher):
    web.pages["https://example.test/big"] = (200, {"content-type": "text/plain", "content-length": "999999"}, b"x")
    assert fetcher.fetch("https://example.test/big").outcome == "too_large"


@pytest.mark.parametrize("exc", [httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow")])
def test_timeouts_reported(web, fetcher, exc):
    web.pages["https://example.test/slow"] = exc
    result = fetcher.fetch("https://example.test/slow")
    assert result.outcome == "timeout" and not result.ok


def test_total_time_budget(web):
    web.html("https://example.test/p", "<p>hi</p>")
    f = Fetcher(Settings(fetch_total_timeout=-1), transport=httpx.MockTransport(web.handler),
                resolver=fake_resolver)
    assert f.fetch("https://example.test/p").outcome == "timeout"


def test_connection_error_and_http_error_and_content_type(web, fetcher):
    web.pages["https://example.test/down"] = httpx.ConnectError("refused")
    web.html("https://example.test/gone", "x", status=410)
    web.pages["https://example.test/doc.pdf"] = (200, {"content-type": "application/pdf"}, b"%PDF")
    assert fetcher.fetch("https://example.test/down").outcome == "connect_error"
    gone = fetcher.fetch("https://example.test/gone")
    assert (gone.outcome, gone.status_code) == ("http_error", 410)
    assert fetcher.fetch("https://example.test/doc.pdf").outcome == "unsupported_content_type"
