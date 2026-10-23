"""Regression tests for: no environment proxies, bounded DNS lookups, total deadline per hop.
FIXTURE transports/resolvers only; the 'hanging resolver' is a thread blocked on an Event."""

import threading
import time
from types import SimpleNamespace

import httpx
import pytest

import backend.web as webmod
from backend import db
from backend.config import Settings
from backend.ingest import ingest_url
from backend.web import DNSTimeoutError, Fetcher, bounded_resolve
from tests.conftest import fake_resolver


def hanging_resolver(release: threading.Event, hang_for=("slow.test",)):
    def resolve(host, port):
        if host in hang_for:
            release.wait(timeout=10)
        return fake_resolver(host, port)

    return resolve


@pytest.fixture
def release():
    event = threading.Event()
    yield event
    event.set()  # let abandoned lookup threads finish


def test_http_client_ignores_environment_proxies(monkeypatch, web):
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    seen = {}
    real_client = httpx.Client

    def spy(*args, **kwargs):
        seen.update(kwargs)
        return real_client(*args, **kwargs)

    monkeypatch.setattr(httpx, "Client", spy)
    web.html("https://example.test/p", "<p>hi</p>")
    f = Fetcher(Settings(), transport=httpx.MockTransport(web.handler), resolver=fake_resolver)
    assert f.fetch("https://example.test/p").ok
    assert seen["trust_env"] is False


def test_dns_lookup_is_bounded_and_reported_as_timeout(web, release):
    f = Fetcher(Settings(fetch_dns_timeout=0.2), transport=httpx.MockTransport(web.handler),
                resolver=hanging_resolver(release))
    started = time.monotonic()
    result = f.fetch("https://slow.test/page")
    assert time.monotonic() - started < 3  # did not wait for the 10s hang
    assert (result.outcome, result.ok) == ("timeout", False) and "DNS lookup" in result.reason
    assert web.requests == []  # nothing was requested


def test_dns_timeout_is_persisted_as_failed_attempt_and_event(conn, web, release):
    s = Settings(fetch_dns_timeout=0.2)
    f = Fetcher(s, transport=httpx.MockTransport(web.handler), resolver=hanging_resolver(release))
    run = db.create_run(conn, "Q?")["id"]
    result = ingest_url(conn, f, run, "https://slow.test/page", s)
    assert (result.status, result.outcome) == ("failed", "timeout")
    (attempt,) = db.list_fetch_attempts(conn, run)
    assert (attempt["outcome"], attempt["source_id"]) == ("timeout", None) and "DNS lookup" in attempt["reason"]
    assert db.list_events(conn, run)[-1]["payload"]["outcome"] == "timeout"


def test_dns_timeout_on_a_redirect_hop(web, release):
    web.pages["https://example.test/r"] = (302, {"location": "https://slow.test/next"}, b"")
    f = Fetcher(Settings(fetch_dns_timeout=0.2), transport=httpx.MockTransport(web.handler),
                resolver=hanging_resolver(release))
    result = f.fetch("https://example.test/r")
    assert result.outcome == "timeout" and result.final_url == "https://slow.test/next"
    assert web.requests == ["https://example.test/r"]


def test_dns_timeout_api_status_is_504(client, web, release, settings, monkeypatch):
    from backend.api.runs import get_fetcher
    from backend.main import app

    s = settings.model_copy(update={"fetch_dns_timeout": 0.2})
    app.dependency_overrides[get_fetcher] = lambda: Fetcher(
        s, transport=httpx.MockTransport(web.handler), resolver=hanging_resolver(release))
    run_id = client.post("/runs", json={"question": "dns timeout?"}).json()["id"]
    resp = client.post(f"/runs/{run_id}/sources", json={"url": "https://slow.test/x"})
    assert resp.status_code == 504 and resp.json()["detail"]["outcome"] == "timeout"


def test_lookup_slots_are_limited(monkeypatch):
    monkeypatch.setattr(webmod, "_lookup_slots", threading.BoundedSemaphore(1))
    webmod._lookup_slots.acquire()  # a stuck lookup holds the only slot
    with pytest.raises(DNSTimeoutError, match="too many DNS lookups"):
        bounded_resolve(fake_resolver, "example.test", 443, 0.1)
    with pytest.raises(DNSTimeoutError):
        bounded_resolve(fake_resolver, "example.test", 443, 0)


def test_resolver_exceptions_cross_the_thread_boundary():
    def failing(host, port):
        raise webmod.UnsafeURLError("nope")

    with pytest.raises(webmod.UnsafeURLError, match="nope"):
        bounded_resolve(failing, "x.test", 80, 1)
    assert bounded_resolve(fake_resolver, "example.test", 443, 1) == ["93.184.216.34"]


def test_total_deadline_is_enforced_between_redirect_hops(monkeypatch, web):
    clock = [1000.0]
    monkeypatch.setattr(webmod, "time", SimpleNamespace(monotonic=lambda: clock[0]))

    def handler(request):
        clock[0] += 100  # the first hop "takes" 100s
        return httpx.Response(302, headers={"location": "https://example.test/two"})

    f = Fetcher(Settings(fetch_total_timeout=30), transport=httpx.MockTransport(handler), resolver=fake_resolver)
    result = f.fetch("https://example.test/one")
    assert result.outcome == "timeout" and "before hop 2" in result.reason

