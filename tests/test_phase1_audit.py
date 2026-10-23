"""Tests added by the Phase 1 audit (docs/PHASE1_REVIEW.md), each for a gap the mutation run or probing found.
All HTTP/DNS here is FIXTURE (MockTransport / fake resolver) unless a test says it uses the real resolver."""

import gzip

import httpx
import pytest

from backend import db
from backend.config import Settings
from backend.ingest import ingest_url
from backend.web import Fetcher, UnsafeURLError, validate_url
from tests.conftest import fake_resolver

ARTICLE = "<html><head><title>T</title></head><body>" + "".join(
    f"<p>Paragraph {i} states fact {i} clearly.</p>" for i in range(12)) + "</body></html>"
URL = "https://example.test/article"


# --- fetch attempts and events are persisted with the right content ---------------------------------


def test_every_outcome_is_persisted_as_attempt_and_event(conn, web, fetcher, settings):
    run = db.create_run(conn, "Q?")["id"]
    web.html(URL, ARTICLE)
    web.html("https://example.test/mirror", ARTICLE)
    web.html("https://example.test/gone", "x", status=410)

    ok = ingest_url(conn, fetcher, run, URL, settings)
    dup_url = ingest_url(conn, fetcher, run, URL, settings)
    dup_content = ingest_url(conn, fetcher, run, "https://example.test/mirror", settings)
    gone = ingest_url(conn, fetcher, run, "https://example.test/gone", settings)
    blocked = ingest_url(conn, fetcher, run, "http://127.0.0.1/", settings)

    assert [r.status for r in (ok, dup_url, dup_content, gone, blocked)] == [
        "ingested", "duplicate", "duplicate", "failed", "failed"]
    attempts = db.list_fetch_attempts(conn, run)
    rows = [(a["url"], a["outcome"], a["status_code"], a["source_id"]) for a in attempts]
    assert rows == [
        (URL, "ok", 200, ok.source_id),
        (URL, "duplicate_url", None, ok.source_id),
        ("https://example.test/mirror", "duplicate_content", None, ok.source_id),  # provenance kept
        ("https://example.test/gone", "http_error", 410, None),
        ("http://127.0.0.1/", "blocked", None, None),
    ]
    assert attempts[3]["reason"] == "HTTP 410" and "non-public address 127.0.0.1" in attempts[4]["reason"]
    assert [a["id"] for a in attempts[3:]] == [gone.fetch_attempt_id, blocked.fetch_attempt_id]

    events = db.list_events(conn, run)
    assert [e["event_type"] for e in events] == [
        "run_created", "source_ingested", "source_duplicate", "source_duplicate", "fetch_failed", "fetch_failed"]
    assert events[3]["payload"] == {"url": "https://example.test/mirror", "outcome": "duplicate_content",
                                    "source_id": ok.source_id}
    assert [e["payload"]["outcome"] for e in events[4:]] == ["http_error", "blocked"]
    assert len(db.list_sources(conn, run)) == 1  # nothing extra stored for duplicates or failures


def test_concurrent_duplicate_is_reported_not_crashed(conn, web, settings):
    """Simulates the race: another request stores the same page while this one is still fetching."""
    run = db.create_run(conn, "Q?")["id"]
    web.html(URL, ARTICLE)

    class RacingFetcher(Fetcher):
        def fetch(self, url):
            result = super().fetch(url)
            with db.tx(conn):  # the competing request commits first
                db.insert_source(conn, run, requested_url=url, final_url=url, title="T", content_type="text/html",
                                 content_hash="competitor", snapshot="x", passages=[(0, 1)])
            return result

    racing = RacingFetcher(settings, transport=httpx.MockTransport(web.handler), resolver=fake_resolver)
    result = ingest_url(conn, racing, run, URL, settings)
    assert (result.status, result.outcome) == ("duplicate", "duplicate_url")
    assert len(db.list_sources(conn, run)) == 1 and len(db.list_passages(conn, run)) == 1


# --- size limits, including the cases the declared Content-Length cannot protect -----------------------


def _fetcher(web_handler, **overrides):
    s = Settings(fetch_max_bytes=50_000, **overrides)
    return Fetcher(s, transport=httpx.MockTransport(web_handler), resolver=fake_resolver)


def test_size_limit_applies_to_streamed_body_without_content_length():
    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=(b"a" * 10_000 for _ in range(10)))

    result = _fetcher(handler).fetch("https://example.test/stream")
    assert result.outcome == "too_large" and "body exceeds" in result.reason  # not the declared-size message


def test_size_limit_applies_to_decompressed_size():
    bomb = gzip.compress(b"a" * 5_000_000)
    assert len(bomb) < 50_000  # small on the wire, so a Content-Length check alone would pass it

    def handler(request):
        return httpx.Response(200, headers={"content-type": "text/plain", "content-encoding": "gzip"}, content=bomb)

    assert _fetcher(handler).fetch("https://example.test/bomb").outcome == "too_large"


def test_configured_timeouts_are_applied_to_the_http_client():
    seen = {}

    def handler(request):
        seen.update(request.extensions["timeout"])
        return httpx.Response(200, headers={"content-type": "text/plain"}, content=b"ok")

    _fetcher(handler, fetch_connect_timeout=2.0, fetch_read_timeout=3.0).fetch("https://example.test/t")
    assert seen == {"connect": 2.0, "read": 3.0, "write": 3.0, "pool": 3.0}


# --- destination rules ---------------------------------------------------------------------------------


@pytest.mark.parametrize("location", [
    "file:///etc/passwd", "ftp://example.test/x", "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest",
    "http://example.test:8080/", "http://user:pw@example.test/", "http://internal.test/", "http://example.test/a b",
])
def test_unsafe_redirect_targets_are_never_requested(web, fetcher, location):
    web.pages["https://example.test/start"] = (302, {"location": location}, b"")
    web.pages["http://example.test/a b"] = (200, {"content-type": "text/plain"}, b"x")
    result = fetcher.fetch("https://example.test/start")
    assert result.outcome == "blocked" and not result.ok
    assert web.requests == ["https://example.test/start"]


def test_unsafe_hop_later_in_a_redirect_chain_is_blocked(web, fetcher):
    web.pages["https://example.test/1"] = (301, {"location": "https://other.test/2"}, b"")
    web.pages["https://other.test/2"] = (302, {"location": "http://loopback.test/3"}, b"")
    result = fetcher.fetch("https://example.test/1")
    assert result.outcome == "blocked" and "127.0.0.1" in result.reason
    assert web.requests == ["https://example.test/1", "https://other.test/2"]


@pytest.mark.parametrize("url", [
    "http://[64:ff9b::7f00:1]/",  # NAT64 form of 127.0.0.1
    "http://[64:ff9b::a00:1]/",  # NAT64 form of 10.0.0.1
    "http://[2002:7f00:1::]/",  # 6to4 form of 127.0.0.1
    "http://[2001:0:4136:e378:8000:63bf:3fff:fdd2]/",  # Teredo
    "http://[::ffff:10.0.0.1]/",
    "http://[fc00::1]/", "http://[fe80::1]/", "http://[::]/", "http://0.0.0.0/", "http://224.0.0.1/",
])
def test_ipv6_and_embedded_ipv4_forms_of_private_addresses_rejected(url):
    with pytest.raises(UnsafeURLError, match="non-public"):
        validate_url(url, fake_resolver)


def test_nat64_form_of_a_public_address_is_still_allowed():
    validate_url("http://[64:ff9b::5db8:d822]/", fake_resolver)  # 93.184.216.34


def test_loopback_rejection_by_address_rule_on_default_port():
    # Default port 80, so the refusal must come from the private-address rule, not the port rule.
    with pytest.raises(UnsafeURLError, match=r"non-public address 127\.0\.0\.1"):
        validate_url("http://127.0.0.1/", fake_resolver)


@pytest.mark.parametrize("url", ["http://example.test/a\nb", "http://example.test/a\x00b", "http://example.test/a\tb",
                                 "http://exa mple.test/"])
def test_whitespace_and_control_characters_rejected(url):
    with pytest.raises(UnsafeURLError, match="whitespace or control"):
        validate_url(url, fake_resolver)


def test_overlong_idna_label_is_a_rejection_not_a_crash():
    # Uses the REAL default resolver; the label is rejected locally before any network lookup.
    with pytest.raises(UnsafeURLError, match="cannot resolve"):
        validate_url("http://" + "a" * 70 + ".example/")


# --- API validation and run isolation ----------------------------------------------------------------------


def test_blank_inputs_and_out_of_range_ids_are_422_not_500(client):
    assert client.post("/runs", json={"question": "      "}).status_code == 422
    run_id = client.post("/runs", json={"question": "  padded question  "}).json()["id"]
    assert client.get(f"/runs/{run_id}").json()["question"] == "padded question"
    assert client.post(f"/runs/{run_id}/sources", json={"url": "   "}).status_code == 422
    assert client.post(f"/runs/{run_id}/sources", json={"url": "http://example.test/a\x00b"}).status_code == 422
    huge = 99999999999999999999
    for path in (f"/runs/{huge}", f"/runs/{huge}/sources", f"/runs/{huge}/passages", f"/runs/{huge}/events",
                 "/runs/0", "/runs/-1", f"/runs/{run_id}/passages?source_id={huge}", f"/runs/{run_id}/passages?source_id=0"):
        assert client.get(path).status_code == 422, path
    assert client.post(f"/runs/{huge}/sources", json={"url": URL}).status_code == 422
    assert client.get("/runs/abc").status_code == 422


def test_source_filter_cannot_reach_another_runs_passages_or_events(client, web):
    web.html(URL, ARTICLE)
    a = client.post("/runs", json={"question": "run A question"}).json()["id"]
    b = client.post("/runs", json={"question": "run B question"}).json()["id"]
    source_a = client.post(f"/runs/{a}/sources", json={"url": URL}).json()["source"]["id"]
    assert client.get(f"/runs/{a}/passages?source_id={source_a}").json() != []
    assert client.get(f"/runs/{b}/passages?source_id={source_a}").json() == []
    assert client.get(f"/runs/{b}/sources").json() == []
    assert [e["event_type"] for e in client.get(f"/runs/{b}/events").json()] == ["run_created"]


# --- safe repeated initialisation, including upgrading an older database -------------------------------------


def test_reinitialising_and_upgrading_an_older_schema_keeps_data(tmp_path):
    path = tmp_path / "old.db"
    c = db.connect(path)
    run = db.create_run(c, "Survives upgrade?")
    # Make it look like a schema-version-1 database: no Phase 2 tables, older user_version.
    c.execute("DROP TABLE model_calls")
    c.execute("DROP TABLE research_selections")
    c.execute("PRAGMA user_version = 1")
    c.close()

    c = db.connect(path)  # must create the missing tables without touching existing rows
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"model_calls", "research_selections", "runs", "passages", "fetch_attempts", "audit_events"} <= tables
    assert db.get_run(c, run["id"])["question"] == "Survives upgrade?"
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    c.close()
    db.connect(path).close()  # and a third open is a no-op
