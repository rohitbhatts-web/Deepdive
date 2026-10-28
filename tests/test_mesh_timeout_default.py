"""The per-request deadline default is 90 s (it was 60 s). Offline: SQLite, a fake clock and the real adapter against a provider on localhost, with the 60 s and 90 s deadlines
SCALED to 0.8 s and 1.2 s (the same 2:3 ratio) so nothing waits a minute. This shows the mechanics of the deadline; it does not show that Mesh is faster or more reliable."""

import json
import time

import pytest
from pydantic import BaseModel

from backend import db
from backend.budget import Budget, BudgetExceeded
from backend.config import Settings
from backend.llm import LLM, ModelCallError
from backend.mesh_client import traced_async_client
from tests import test_transport_diagnostics as td

OLD, NEW = 0.8, 1.2  # the old and new deadlines at 1/75 scale
DELAY = 1.0  # a reply that arrives after the old deadline and before the new one (the observed 61 to 66 s replies sit between 60 s and 90 s)


class Answer(BaseModel):
    ok: str


@pytest.fixture(scope="module", autouse=True)
def warm_transport():
    """One throwaway request with a generous deadline, so the first use of httpx/httpcore in the process (lazy imports, SSL context) cannot eat into the 0.8 s test deadlines."""
    import asyncio

    provider = td.Provider(td.ok())
    try:
        client = traced_async_client(td.KEY, f"http://127.0.0.1:{provider.port}/v1", 30.0)

        async def once():
            try:
                await client.chat.completions.create(model="m", messages=[{"role": "user", "content": "x"}])
            finally:
                await client.close()

        asyncio.run(once())
    finally:
        provider.close()


@pytest.fixture
def build(conn, settings):
    providers = []

    def make(*script, **overrides):
        s = settings.model_copy(update={"mesh_api_key": td.KEY, **overrides})
        provider = td.Provider(*script)
        providers.append(provider)
        url = f"http://127.0.0.1:{provider.port}/v1"
        llm = LLM(None, s, frozenset(), async_client_factory=lambda: traced_async_client(td.KEY, url, s.mesh_timeout))
        return llm, Budget(s), provider, db.create_run(conn, "question")["id"], s

    yield make
    for p in providers:
        p.close()


def call(llm, conn, budget, run_id):
    return llm.call(conn, run_id, budget, agent="planner", model="m", system="You are the Planner", user="u", schema=Answer)


# ---------------------------------------------------------------- the default


def test_the_default_is_90_seconds_and_an_environment_value_still_overrides_it(monkeypatch):
    monkeypatch.delenv("MESH_TIMEOUT", raising=False)
    assert Settings(_env_file=None).mesh_timeout == 90.0
    assert Settings(_env_file=None).max_elapsed_seconds == 300.0 and Settings(_env_file=None).min_call_seconds == 10.0  # the job budget is unchanged
    monkeypatch.setenv("MESH_TIMEOUT", "45")
    assert Settings(_env_file=None).mesh_timeout == 45.0


# ---------------------------------------------------------------- slow replies


def test_a_reply_after_the_old_deadline_and_before_the_new_one_fails_at_the_old_and_succeeds_at_the_new(conn, build):
    llm, budget, provider, run_id, _ = build(("delay", DELAY, td.ok()), mesh_timeout=OLD)
    with pytest.raises(ModelCallError, match="wall-clock deadline of 0.8s"):
        call(llm, conn, budget, run_id)
    assert td.diag(conn, td.rows(conn)[0]["id"])["error"]["stopped_in_phase"] == "receive_response_headers"

    llm, budget, provider, run_id, _s = build(("delay", DELAY, td.ok()), mesh_timeout=NEW)
    started = time.monotonic()
    assert call(llm, conn, budget, run_id).ok == "yes"
    elapsed = time.monotonic() - started
    assert DELAY <= elapsed < NEW and len(provider.requests) == 1  # it waited past the old deadline, replied inside the new one, one request
    row = td.rows(conn)[-1]
    assert row["ok"] == 1 and row["latency_ms"] >= DELAY * 1000 - 50
    d = td.diag(conn, row["id"])
    assert d["outcome"] == "ok" and d["deadline_s"] == pytest.approx(NEW, abs=0.01) and d["transport"]["http_requests_sent"] == 1


# ---------------------------------------------------------------- the remaining job time still caps every request


@pytest.mark.parametrize("elapsed, expected", [(0, 90.0), (200, 90.0), (210, 90.0), (215, 85.0), (289.9, 10.1)])
def test_a_request_never_gets_more_than_the_job_time_left_with_the_real_defaults(elapsed, expected):
    t = [0.0]
    b = Budget(Settings(mesh_api_key="", _env_file=None), clock=lambda: t[0])
    t[0] = elapsed
    assert b.call_timeout() == pytest.approx(expected)


def test_with_the_real_defaults_no_request_starts_with_less_than_ten_seconds_left_or_after_the_limit():
    t = [0.0]
    b = Budget(Settings(mesh_api_key="", _env_file=None), clock=lambda: t[0])
    for elapsed, message in ((290.5, "insufficient time remaining"), (299.9, "insufficient time remaining"), (300.0, "max_elapsed_seconds reached"), (420.0, "max_elapsed_seconds reached")):
        t[0] = elapsed
        with pytest.raises(BudgetExceeded, match=message):
            b.call_timeout()


def test_the_remaining_job_time_cuts_a_request_short_even_though_the_deadline_would_allow_it(conn, build):
    # the same reply (DELAY 1.0 s) succeeds with plenty of job time left and fails when only 0.9 s of the job remains, although mesh_timeout is 1.2 s
    llm, budget, _, run_id, _ = build(("delay", DELAY, td.ok()), mesh_timeout=NEW, max_elapsed_seconds=100.0)
    assert call(llm, conn, budget, run_id).ok == "yes"
    llm, budget, provider, run_id, _ = build(("delay", DELAY, td.ok()), mesh_timeout=NEW, max_elapsed_seconds=0.9, min_call_seconds=0.3)
    started = time.monotonic()
    with pytest.raises(ModelCallError, match=r"wall-clock deadline of 0\.[89]"):
        call(llm, conn, budget, run_id)
    assert time.monotonic() - started < 1.15  # stopped at the job's time, not at the 1.2 s deadline or when the reply came
    d = td.diag(conn, td.rows(conn)[-1]["id"])
    assert 0.8 < d["deadline_s"] <= 0.9 and d["failure_stage"] == "outer_deadline" and d["mesh_timeout_s"] == NEW
    assert len(provider.requests) == 1


# ---------------------------------------------------------------- expiry cancels local work, retries nothing, starts nothing downstream


def test_expiry_at_the_new_deadline_cancels_the_request_and_is_not_retried(conn, build):
    llm, budget, provider, run_id, s = build(("silent",), mesh_timeout=NEW)
    started = time.monotonic()
    with pytest.raises(ModelCallError, match="wall-clock deadline of 1.2s"):
        call(llm, conn, budget, run_id)
    assert NEW - 0.1 <= time.monotonic() - started < NEW + 1.5
    assert provider.client_closed.wait(3)  # the connection was closed: no local request keeps running
    (row,) = td.rows(conn)
    assert row["ok"] == 0 and row["prompt_tokens"] is None and row["request_id"] is None and budget.model_requests == 1
    time.sleep(1.0)
    assert len(td.rows(conn)) == 1 and len(provider.requests) == 1  # nothing was retried in the background
    assert budget.output_tokens == s.max_output_tokens


def test_a_planner_timeout_at_the_new_deadline_starts_no_search_fetch_or_other_stage(tmp_path, settings, fetcher):
    from backend.graph import execute_research
    from backend.services import ResearchServices
    from tests.fakes import FakeSearch

    path = tmp_path / "job.db"
    c = db.connect(path)
    s = settings.model_copy(update={"mesh_api_key": td.KEY, "mesh_timeout": NEW})
    provider = td.Provider(("silent",))
    try:
        url = f"http://127.0.0.1:{provider.port}/v1"
        llm = LLM(None, s, frozenset(), async_client_factory=lambda: traced_async_client(td.KEY, url, s.mesh_timeout))
        search = FakeSearch({})
        run_id = db.create_run(c, "How do Git merge and Git rebase differ, and when should a developer use each?")["id"]
        with db.tx(c):
            db.claim_run_for_research(c, run_id)
        c.close()
        execute_research(run_id, ResearchServices(s, llm, search, fetcher, lambda: db.connect(path)), verify=True, complete=True)
    finally:
        provider.close()
    c = db.connect(path)
    run = db.get_run(c, run_id)
    assert run["status"] == "failed" and run["stop_reason"].startswith("planner failed:") and "wall-clock deadline of 1.2s" in run["stop_reason"]
    assert search.queries == [] and len(provider.requests) == 1
    for table in ("searches", "sources", "fetch_attempts", "passages", "claims", "reports", "subquestions"):
        assert c.execute(f"SELECT count(*) FROM {table}").fetchone()[0] == 0, table
    assert [r["agent"] for r in c.execute("SELECT agent FROM model_calls")] == ["planner"]
    events = [r["event_type"] for r in c.execute("SELECT event_type FROM audit_events ORDER BY id")]
    assert events[-2:] == ["research_finished", "verification_skipped"]
    assert json.loads(c.execute("SELECT payload FROM audit_events WHERE event_type = 'research_finished'").fetchone()["payload"])["budget_used_of_limit"]["model_requests"][0] == 1
    c.close()
