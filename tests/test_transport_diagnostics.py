"""The model-request path through the REAL adapter (AsyncOpenAI + httpx + httpcore, the LLM's own deadline and persistence) against a deterministic provider on localhost.

No live provider is contacted: the fake listens on 127.0.0.1 only, with short test deadlines instead of 60 s. These tests show what the code observes and records for each
failure shape (delay beyond the deadline, 5xx, billing/quota/rate-limit/auth statuses, malformed or incomplete output, transport failure, cancellation) and that retry,
request accounting, request ids and unknown usage behave as configured. They do NOT show why a real provider is slow, and nothing here proves live reliability."""

import json
import socket
import threading
import time

import pytest
from pydantic import BaseModel

from backend import db
from backend.budget import Budget
from backend.llm import LLM, ModelCallError
from backend.mesh_client import traced_async_client
from backend.transport_trace import (
    TransportTrace,
    clean_header_value,
    header_names,
    parse_mesh_headers,
    safe_headers,
)

KEY = "TESTVALUE-not-a-real-key-789"


class Answer(BaseModel):
    ok: str


def completion(content: str, usage=True) -> bytes:
    body = {"id": "x", "object": "chat.completion", "created": 0, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}]}
    if usage:
        body["usage"] = {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}
    return json.dumps(body).encode()


def ok(content='{"ok": "yes"}', headers=None):
    return ("reply", 200, {"x-request-id": "req_OK", "x-ratelimit-remaining-requests": "99", **(headers or {})}, completion(content))


def refusal(status, headers, error):
    return ("reply", status, headers, json.dumps({"error": error}).encode())


class Provider:
    """One scripted behaviour per connection, in order: ('reply', status, headers, body), ('silent',), ('delay', seconds, behaviour). Records every request it receives."""

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.client_closed = threading.Event()
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.stop = threading.Event()
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        conn.settimeout(0.1)
        try:
            data = b""
            while b"\r\n\r\n" not in data:
                data += conn.recv(65536)
            head, _, rest = data.partition(b"\r\n\r\n")
            headers = {k.lower(): v for k, _, v in (line.decode().partition(": ") for line in head.split(b"\r\n")[1:])}
            need = int(headers.get("content-length", 0))
            while len(rest) < need:
                rest += conn.recv(65536)
            self.requests.append({"path": head.split(b" ")[1].decode(), "authorization": headers.get("authorization"), "body": rest})
            behaviour = self.script.pop(0) if self.script else ("silent",)
            if behaviour[0] == "delay":
                time.sleep(behaviour[1])
                behaviour = behaviour[2]
            if behaviour[0] == "reply":
                _, status, extra, body = behaviour
                lines = "".join(f"{k}: {v}\r\n" for k, v in {"Content-Type": "application/json", **extra}.items())
                conn.sendall(f"HTTP/1.1 {status} Reason\r\n{lines}Content-Length: {len(body)}\r\n\r\n".encode() + body)
                return
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not self.stop.is_set():
                try:
                    if conn.recv(1) == b"":
                        self.client_closed.set()
                        return
                except TimeoutError:
                    pass
        except OSError:
            self.client_closed.set()
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.sock.close()


@pytest.fixture
def harness(conn, settings):
    providers = []

    def build(*script, port=None, **overrides):
        s = settings.model_copy(update={"mesh_api_key": KEY, **overrides})
        provider = Provider(*script)
        providers.append(provider)
        url = f"http://127.0.0.1:{port or provider.port}/v1"
        llm = LLM(None, s, frozenset(), async_client_factory=lambda: traced_async_client(KEY, url, s.mesh_timeout))
        return llm, Budget(s), provider, db.create_run(conn, "question")["id"], s

    yield build
    for p in providers:
        p.close()


def run_call(llm, conn, budget, run_id):
    return llm.call(conn, run_id, budget, agent="planner", model="m", system="You are the Planner", user="u", schema=Answer)


def rows(conn):
    return [dict(r) for r in conn.execute("SELECT * FROM model_calls ORDER BY id")]


def diag(conn, call_id=None):
    stored = {r["model_call_id"]: json.loads(r["diagnostics"]) for r in conn.execute("SELECT * FROM model_call_diagnostics")}
    return stored if call_id is None else stored[call_id]


# ---------------------------------------------------------------- valid and slow-but-successful


def test_a_valid_reply_is_recorded_with_what_the_transport_observed(conn, harness):
    llm, budget, provider, run_id, s = harness(ok())
    assert run_call(llm, conn, budget, run_id).ok == "yes"
    (row,) = rows(conn)
    assert row["ok"] == 1 and row["request_id"] == "req_OK" and row["prompt_tokens"] == 7 and row["completion_tokens"] == 3
    d = diag(conn, row["id"])
    t = d["transport"]
    assert d["outcome"] == "ok" and d["failure_stage"] is None and d["deadline_s"] == pytest.approx(s.mesh_timeout, abs=0.01)
    assert t["http_requests_sent"] == 1 and t["connect_attempts"] == 1 and t["status_code"] == 200 and t["open_phase"] is None and t["failed_phase"] is None
    assert {"connect_tcp", "send_request_headers", "send_request_body", "receive_response_headers", "receive_response_body"} <= set(t["phases_ms"])
    assert t["response_headers"] == {"content-type": "application/json", "x-request-id": "req_OK", "x-ratelimit-remaining-requests": "99"}
    assert d["retries"] == {"sdk": 0, "validation_retry_number": 0} and len(provider.requests) == 1  # one logical request, one HTTP request


def test_a_slow_reply_inside_the_deadline_shows_where_the_time_went(conn, harness):
    llm, budget, _provider, run_id, _ = harness(("delay", 0.5, ok()), mesh_timeout=5.0)
    run_call(llm, conn, budget, run_id)
    (row,) = rows(conn)
    t = diag(conn, row["id"])["transport"]["phases_ms"]
    waited = t["receive_response_headers"][1] - t["receive_response_headers"][0]
    assert waited >= 450 and (t["connect_tcp"][1] - t["connect_tcp"][0]) < 450  # the wait was for the first byte, not the connection
    assert row["latency_ms"] >= 450


# ---------------------------------------------------------------- delay beyond the deadline, and cancellation


def test_a_reply_that_never_comes_stops_at_the_deadline_names_the_phase_and_cancels_the_connection(conn, harness):
    llm, budget, provider, run_id, s = harness(("silent",), mesh_timeout=1.0)
    started = time.monotonic()
    with pytest.raises(ModelCallError, match="wall-clock deadline of 1s reached"):
        run_call(llm, conn, budget, run_id)
    assert 0.9 <= time.monotonic() - started < 3.0
    assert provider.client_closed.wait(3)  # cancellation closed the connection: no request is left running locally
    (row,) = rows(conn)
    assert row["ok"] == 0 and row["request_id"] is None and row["error"].startswith("TimeoutError: wall-clock deadline of 1s")
    assert row["prompt_tokens"] is None and row["completion_tokens"] is None and row["total_tokens"] is None and row["finish_reason"] is None  # unknown stays unknown
    d = diag(conn, row["id"])
    assert d["failure_stage"] == "outer_deadline" and d["error"]["error_class"] == "deadline" and d["error"]["http_status"] is None
    assert d["error"]["stopped_in_phase"] == "receive_response_headers"  # connected, request written, no byte back
    assert d["transport"]["phases_ms"]["connect_tcp"][1] is not None and d["transport"]["http_requests_sent"] == 1
    assert 900 <= d["elapsed_ms"] < 3000 and d["deadline_s"] == pytest.approx(1.0, abs=0.01)
    time.sleep(1.2)
    assert len(rows(conn)) == 1 and len(provider.requests) == 1  # nothing retried in the background
    assert budget.model_requests == 1 and budget.output_tokens == s.max_output_tokens  # one request counted; the cap reserves its per-call maximum


# ---------------------------------------------------------------- provider refusals: 5xx, billing, quota, rate limit, account


def test_a_503_keeps_its_status_body_headers_and_request_id_and_is_not_retried(conn, harness):
    error = {"code": "upstream_error", "message": "The model provider is temporarily unavailable.", "provider_code": "internal_provider_error"}
    llm, budget, provider, run_id, _ = harness(refusal(503, {"x-request-id": "req_503"}, error))
    with pytest.raises(ModelCallError, match="503"):
        run_call(llm, conn, budget, run_id)
    (row,) = rows(conn)
    assert row["ok"] == 0 and row["request_id"] == "req_503" and "upstream_error" in row["error"] and row["prompt_tokens"] is None
    d = diag(conn, row["id"])
    assert d["failure_stage"] == "http_status" and d["error"]["http_status"] == 503 and d["error"]["error_class"] == "upstream_server_error"
    assert d["error"]["provider_code"] == "upstream_error" and d["transport"]["response_headers"]["x-request-id"] == "req_503"
    assert len(provider.requests) == 1 and budget.model_requests == 1  # no SDK retry, no application retry


@pytest.mark.parametrize("status, klass, code", [(402, "payment_required", "insufficient_credits"), (429, "rate_limited_or_quota", "quota_exceeded"), (403, "forbidden", "account_suspended"), (401, "authentication", "invalid_api_key")])
def test_billing_quota_rate_limit_and_account_refusals_are_kept_distinct_never_turned_into_a_timeout(conn, harness, status, klass, code):
    headers = {"retry-after": "30", "x-ratelimit-remaining-requests": "0", "x-ratelimit-reset-requests": "12s", "x-request-id": f"req_{status}", "set-cookie": "session=SECRET", "x-internal": "no"}
    llm, budget, provider, run_id, _ = harness(refusal(status, headers, {"code": code, "message": f"refused because {code}"}))
    with pytest.raises(ModelCallError) as caught:
        run_call(llm, conn, budget, run_id)
    assert "TimeoutError" not in str(caught.value) and code in str(caught.value) and str(status) in str(caught.value)  # the provider's own words reach the stored error
    (row,) = rows(conn)
    assert row["request_id"] == f"req_{status}" and code in row["error"]
    d = diag(conn, row["id"])
    assert d["failure_stage"] == "http_status" and d["error"]["http_status"] == status and d["error"]["error_class"] == klass and d["error"]["provider_code"] == code
    kept = d["transport"]["response_headers"]
    assert kept["retry-after"] == "30" and kept["x-ratelimit-remaining-requests"] == "0" and kept["x-ratelimit-reset-requests"] == "12s"  # when to retry / what is left survives
    assert "set-cookie" not in kept and "x-internal" not in kept  # only an allow-list of headers is stored
    assert len(provider.requests) == 1  # no retry of any of them


# ---------------------------------------------------------------- malformed or incomplete output, and the one validation retry


def test_malformed_then_incomplete_then_valid_output_uses_the_configured_validation_retry_only(conn, harness):
    llm, budget, provider, run_id, _ = harness(ok("this is not json"), ok('{"wrong_field": 1}'))
    with pytest.raises(Exception, match="did not return valid output after 2 attempts"):
        run_call(llm, conn, budget, run_id)
    first, second = rows(conn)
    assert (first["attempt"], second["attempt"]) == (1, 2) and not first["ok"] and not second["ok"]
    assert diag(conn, first["id"])["failure_stage"] == "parsing" and diag(conn, second["id"])["failure_stage"] == "validation"
    assert diag(conn, second["id"])["retries"]["validation_retry_number"] == 1
    assert len(provider.requests) == 2 and budget.model_requests == 2  # exactly the two attempts the code allows; each is one HTTP request
    # usage the provider reported on these replies is kept, not erased by the validation failure
    assert first["prompt_tokens"] == 7 and first["request_id"] == "req_OK"


def test_a_reply_without_usage_leaves_usage_unknown(conn, harness):
    body = completion('{"ok": "yes"}', usage=False)
    llm, budget, _, run_id, s = harness(("reply", 200, {"x-request-id": "req_NU"}, body))
    run_call(llm, conn, budget, run_id)
    (row,) = rows(conn)
    assert row["ok"] == 1 and row["prompt_tokens"] is None and row["completion_tokens"] is None  # NULL, not 0
    assert budget.output_tokens == s.max_output_tokens  # the cap still reserves the per-call maximum


# ---------------------------------------------------------------- transport failure


def test_a_refused_connection_is_a_transport_failure_in_connect_with_no_http_request(conn, harness):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        dead_port = probe.getsockname()[1]  # nothing listens here once the probe closes
    llm, budget, _provider, run_id, _ = harness(port=dead_port, mesh_timeout=5.0)
    with pytest.raises(ModelCallError):
        run_call(llm, conn, budget, run_id)
    (row,) = rows(conn)
    assert row["ok"] == 0 and row["request_id"] is None and row["prompt_tokens"] is None
    d = diag(conn, row["id"])
    assert d["failure_stage"] == "transport" and d["error"]["error_class"] == "connection" and "ConnectError" in d["error"]["chain"]
    assert d["transport"]["failed_phase"] == "connect_tcp" and d["transport"]["http_requests_sent"] == 0 and d["transport"]["connect_attempts"] >= 1
    assert _provider.requests == []


def test_a_cache_header_is_kept_and_every_response_header_name_is_listed_without_values(conn, harness):
    headers = {"x-cache": "HIT", "age": "5", "set-cookie": "session=SECRET-VALUE", "x-internal-thing": "private-value"}
    llm, budget, _, run_id, _ = harness(ok(headers=headers))
    run_call(llm, conn, budget, run_id)
    t = diag(conn, rows(conn)[0]["id"])["transport"]
    assert t["response_headers"]["x-cache"] == "HIT" and "set-cookie" not in t["response_headers"] and "x-internal-thing" not in t["response_headers"]
    assert {"x-cache", "age", "set-cookie", "x-internal-thing", "x-request-id"} <= set(t["response_header_names"])  # names only
    assert "SECRET-VALUE" not in json.dumps(t) and "private-value" not in json.dumps(t)


# ---------------------------------------------------------------- header allowlist and Mesh routing headers


UNKNOWN_MESH = {"routing_attempts": None, "routing_fallback": None, "provider_latency_ms": None, "byok_fallback_triggered": None, "present": [], "unrecognized": []}
FOUR = ("routing_attempts", "routing_fallback", "provider_latency_ms", "byok_fallback_triggered")


def test_the_mesh_routing_headers_are_parsed_to_validated_values_in_any_header_case():
    got = parse_mesh_headers({"X-Mesh-Routing-Attempts": "3", "X-MESH-ROUTING-FALLBACK": "TRUE", "x-provider-latency-ms": "46012", "X-BYOK-Fallback-Triggered": "false"})
    assert got == {"routing_attempts": 3, "routing_fallback": True, "provider_latency_ms": 46012, "byok_fallback_triggered": False,
                   "present": ["x-mesh-routing-attempts", "x-mesh-routing-fallback", "x-provider-latency-ms", "x-byok-fallback-triggered"], "unrecognized": []}
    assert parse_mesh_headers({"x-provider-latency-ms": "46012.5"})["provider_latency_ms"] == 46012.5
    spaced = parse_mesh_headers({"x-mesh-routing-attempts": " 1 ", "x-mesh-routing-fallback": "false"})
    assert spaced["routing_attempts"] == 1 and spaced["routing_fallback"] is False


def test_a_missing_mesh_header_is_unknown_never_zero_or_false():
    assert parse_mesh_headers({"x-request-id": "req_1", "content-type": "application/json"}) == UNKNOWN_MESH  # absence is not "no fallback" and not "zero attempts"
    only_attempts = parse_mesh_headers({"x-mesh-routing-attempts": "2"})
    assert only_attempts["routing_attempts"] == 2 and only_attempts["routing_fallback"] is None and only_attempts["byok_fallback_triggered"] is None
    assert TransportTrace().summary()["mesh"] == UNKNOWN_MESH  # before any response, everything is unknown


@pytest.mark.parametrize("header, bad", [
    ("x-mesh-routing-attempts", ["abc", "-1", "3.5", "999", "21", "", "3 4", "\u0663", "0x3", "3; DROP TABLE", "1\x00"]),
    ("x-mesh-routing-fallback", ["yes", "1", "0", "maybe", "truee", "", "true; x"]),
    ("x-provider-latency-ms", ["-5", "NaN", "inf", "1e9", "9999999999", "46012.5.5", "ms", "", "7" * 200, "12\x07", "3600001"]),
    ("x-byok-fallback-triggered", ["yes", "no", "1", "on", ""]),
])
def test_malformed_or_unrecognized_mesh_values_are_unknown_and_not_stored(header, bad):
    for value in bad:
        got = parse_mesh_headers({header: value})
        assert [got[k] for k in FOUR] == [None] * 4, (header, value)
        assert got["present"] == [header] and got["unrecognized"] == [header], (header, value)  # it was there, it was not understood, and its value is not kept
        assert value.strip() == "" or value not in json.dumps([got[k] for k in FOUR])  # the rejected value is not stored anywhere in the parsed result


def test_only_exactly_named_headers_are_stored_by_value_and_values_are_bounded():
    headers = {"Retry-After": "30", "x-ratelimit-remaining-requests": "0", "ratelimit-policy": "100;w=60", "age": "5", "X-Cache": "HIT", "x-request-id": "req_A",
               "authorization": "Bearer SECRET", "proxy-authorization": "Basic SECRET", "set-cookie": "s=SECRET", "cookie": "s=SECRET", "x-api-key": "SECRET", "x-auth-token": "SECRET",
               "x-ratelimit-weird": "SECRET", "x-cache-secret": "SECRET", "x-mycache": "SECRET", "ratelimit-custom": "SECRET", "x-mesh-version": "SECRET", "x-cloud-trace-context": "SECRET",
               "x-mesh-routing-attempts": "3", "x-provider-latency-ms": "46012", "content-type": "application/json"}
    kept = safe_headers(headers)
    assert kept == {"retry-after": "30", "x-ratelimit-remaining-requests": "0", "ratelimit-policy": "100;w=60", "age": "5", "x-cache": "HIT", "x-request-id": "req_A", "content-type": "application/json"}
    assert "SECRET" not in json.dumps(kept)  # a prefix or a substring such as "cache" no longer qualifies a header
    assert safe_headers({"retry-after": "9" * 101, "age": "5\x00", "x-cache": "h\u00e9", "x-request-id": "req_ok"}) == {"x-request-id": "req_ok"}  # too long, control character, non-ASCII: dropped whole
    assert clean_header_value(" x ") == "x" and clean_header_value(None) is None and clean_header_value(5) is None


def test_header_names_are_listed_without_values_bounded_and_limited_to_ordinary_names():
    names = header_names({"X-A": "v", "bad name": "v", "bad\nname": "v", "x" * 65: "v"})
    assert names == ["x-a"]  # spaces, newlines and over-long names are not listed
    assert len(header_names({f"h{i}": "v" for i in range(60)})) == 40  # bounded in number
    assert header_names({"Set-Cookie": "v"}) == ["set-cookie"]  # the NAME may be listed; the value never is


def test_through_the_adapter_mesh_values_are_validated_and_sensitive_or_unapproved_headers_are_not_stored(conn, harness):
    headers = {"x-mesh-routing-attempts": "3", "x-mesh-routing-fallback": "true", "x-provider-latency-ms": "46012", "x-byok-fallback-triggered": "perhaps",
               "set-cookie": "session=COOKIE-VALUE", "x-api-key": "APIKEY-VALUE", "x-internal-thing": "INTERNAL-VALUE", "x-mesh-version": "VERSION-VALUE", "x-cache": "MISS"}
    llm, budget, _, run_id, _ = harness(ok(headers=headers))
    run_call(llm, conn, budget, run_id)
    t = diag(conn, rows(conn)[0]["id"])["transport"]
    assert t["mesh"] == {"routing_attempts": 3, "routing_fallback": True, "provider_latency_ms": 46012, "byok_fallback_triggered": None,
                         "present": ["x-mesh-routing-attempts", "x-mesh-routing-fallback", "x-provider-latency-ms", "x-byok-fallback-triggered"], "unrecognized": ["x-byok-fallback-triggered"]}
    assert t["response_headers"] == {"content-type": "application/json", "x-request-id": "req_OK", "x-ratelimit-remaining-requests": "99", "x-cache": "MISS"}
    stored = json.dumps(diag(conn, rows(conn)[0]["id"]))
    for value in ("COOKIE-VALUE", "APIKEY-VALUE", "INTERNAL-VALUE", "VERSION-VALUE", "perhaps", KEY):
        assert value not in stored
    assert {"set-cookie", "x-api-key", "x-internal-thing"} <= set(t["response_header_names"])  # names only


def test_through_the_adapter_a_response_without_mesh_headers_leaves_everything_unknown(conn, harness):
    llm, budget, _, run_id, _ = harness(ok())
    run_call(llm, conn, budget, run_id)
    assert diag(conn, rows(conn)[0]["id"])["transport"]["mesh"] == UNKNOWN_MESH


# ---------------------------------------------------------------- credentials and bodies are never stored


def test_no_key_authorization_header_prompt_or_reply_body_reaches_the_diagnostics(conn, harness):
    llm, budget, provider, run_id, _ = harness(refusal(429, {"retry-after": "5", "authorization": "Bearer " + KEY}, {"code": "quota_exceeded", "message": "slow down"}))
    with pytest.raises(ModelCallError):
        run_call(llm, conn, budget, run_id)
    stored = " ".join(r["diagnostics"] for r in conn.execute("SELECT diagnostics FROM model_call_diagnostics")) + " " + " ".join(str(r["error"]) for r in conn.execute("SELECT error FROM model_calls"))
    assert provider.requests[0]["authorization"] == f"Bearer {KEY}"  # the key IS sent to the provider ...
    assert KEY not in stored and "Bearer" not in stored and "You are the Planner" not in stored and "slow down" not in stored.replace(" ".join(r["error"] for r in conn.execute("SELECT error FROM model_calls")), "")  # ... and is not stored
    # the provider's message is in the error column (that is how a quota refusal stays readable), never duplicated into the diagnostics
    assert "slow down" in conn.execute("SELECT error FROM model_calls").fetchone()["error"]


# ---------------------------------------------------------------- the trace itself, and storage rules


def test_the_trace_reports_the_phase_a_request_is_stuck_in():
    clock = [0.0]
    t = TransportTrace(clock=lambda: clock[0])
    import asyncio

    async def feed(events):
        for ms, name, info in events:
            clock[0] = ms / 1000
            await t.callback(name, info)

    asyncio.run(feed([(0, "connection.connect_tcp.started", {"timeout": 60.0})]))
    assert t.open_phase() == "connect_tcp" and t.connect_timeout_s == 60.0 and t.http_requests_sent == 0  # a request that never got a connection
    asyncio.run(feed([(3200, "connection.connect_tcp.complete", {}), (3201, "http11.send_request_headers.started", {}), (3202, "http11.send_request_headers.complete", {}),
                      (3203, "http11.receive_response_headers.started", {})]))
    s = t.summary()
    assert s["open_phase"] == "receive_response_headers" and s["http_requests_sent"] == 1 and s["phases_ms"]["connect_tcp"] == [0, 3200]
    asyncio.run(feed([(5000, "http11.receive_response_headers.failed", {})]))
    assert t.failed_phase() == "receive_response_headers" and t.open_phase() is None
    asyncio.run(feed([(5001, "connection.unrelated.event", {}), (5002, "malformed", {})]))
    assert t.summary()["http_requests_sent"] == 1  # unknown events are ignored


def test_diagnostics_are_immutable_and_older_calls_simply_have_none(conn):
    run_id = db.create_run(conn, "q")["id"]
    old = db.record_model_call(conn, run_id, agent="planner", model="m", attempt=1, ok=False, latency_ms=5)  # recorded before the table existed: no diagnostics
    new = db.record_model_call(conn, run_id, agent="planner", model="m", attempt=1, ok=False, latency_ms=6)
    db.record_model_call_diagnostics(conn, new, {"outcome": "failed"})
    assert db.model_call_diagnostics(conn, run_id) == {new: {"outcome": "failed"}} and old not in db.model_call_diagnostics(conn, run_id)
    with pytest.raises(Exception, match="immutable"):
        conn.execute("UPDATE model_call_diagnostics SET diagnostics = '{}'")
    with pytest.raises(Exception, match="never deleted"):
        conn.execute("DELETE FROM model_call_diagnostics")


# ---------------------------------------------------------------- planner failure through the real graph: nothing downstream starts


def test_a_planner_timeout_through_the_real_graph_starts_no_search_fetch_or_other_work(tmp_path, settings, fetcher, harness_factory=None):
    from backend.graph import execute_research
    from backend.services import ResearchServices
    from tests.fakes import FakeSearch

    path = tmp_path / "job.db"
    c = db.connect(path)
    s = settings.model_copy(update={"mesh_api_key": KEY, "mesh_timeout": 1.0})
    provider = Provider(("silent",))
    try:
        url = f"http://127.0.0.1:{provider.port}/v1"
        llm = LLM(None, s, frozenset(), async_client_factory=lambda: traced_async_client(KEY, url, s.mesh_timeout))
        search = FakeSearch({})
        run_id = db.create_run(c, "How do Git merge and Git rebase differ, and when should a developer use each?")["id"]
        with db.tx(c):
            db.claim_run_for_research(c, run_id)
        c.close()
        svc = ResearchServices(s, llm, search, fetcher, lambda: db.connect(path))
        execute_research(run_id, svc, verify=True, complete=True)
    finally:
        provider.close()
    c = db.connect(path)
    run = db.get_run(c, run_id)
    assert run["status"] == "failed" and run["stop_reason"].startswith("planner failed:") and "wall-clock deadline of 1s" in run["stop_reason"]
    assert search.queries == [] and len(provider.requests) == 1  # no search, and the provider saw exactly one request
    for table in ("searches", "sources", "fetch_attempts", "passages", "claims", "reports", "subquestions"):
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert [r["agent"] for r in c.execute("SELECT agent FROM model_calls")] == ["planner"]
    events = [r["event_type"] for r in c.execute("SELECT event_type FROM audit_events ORDER BY id")]
    assert events[-2:] == ["research_finished", "verification_skipped"] and "plan_created" not in events
    finished = next(json.loads(r["payload"]) for r in c.execute("SELECT payload FROM audit_events WHERE event_type = 'research_finished'"))
    assert finished["status"] == "failed" and finished["budget_used_of_limit"]["model_requests"][0] == 1
    c.close()
