"""Per-call deadlines, honest accounting of timed-out requests, preserved evidence and the explicit retry from stored evidence.
Model replies and the clock are SCRIPTED FIXTURES: these tests prove orchestration and bookkeeping, not model quality or provider latency."""

import pytest
from pydantic import BaseModel

from backend import claims, db
from backend.api.completion import get_retry_services
from backend.budget import Budget, BudgetExceeded
from backend.config import Settings
from backend.llm import LLM, ModelCallError
from backend.main import app
from backend.services import ResearchServices
from tests.fakes import (
    FakeMesh,
    FakeSearch,
    claim,
    coverage,
    draft,
    obligations,
    synth,
    verdict,
)
from tests.seed import Q0, Q2, seed_collected_run

TIMEOUT = TimeoutError("Request timed out.")


class Out(BaseModel):
    x: int


def fake_clock(start=0.0):
    t = [start]
    return (lambda: t[0]), t


def budget_at(elapsed, **kw):
    s = Settings(mesh_api_key="", max_elapsed_seconds=100.0, mesh_timeout=60.0, min_call_seconds=10.0, **kw)
    clock, t = fake_clock()
    b = Budget(s, clock)
    t[0] = elapsed
    return b, s


def call(llm, conn, run_id, b):
    return llm.call(conn, run_id, b, agent="planner", model="fx", system="You are the Planner", user="u", schema=Out)


# --- per-call deadline -------------------------------------------------------------------------------------------------------------------

def test_a_request_may_not_outlast_the_job_deadline():
    for elapsed, expected in ((10, 60.0), (40, 60.0), (70, 30.0), (89, 11.0)):
        b, _ = budget_at(elapsed)
        assert b.call_timeout() == pytest.approx(expected)
    for elapsed in (90.5, 95, 99.9):  # fewer than min_call_seconds (10 s) left, deadline not yet reached
        b, _ = budget_at(elapsed)
        with pytest.raises(BudgetExceeded, match="insufficient time remaining"):
            b.call_timeout()
    for elapsed in (100, 150):  # the deadline itself has passed: the established message
        b, _ = budget_at(elapsed)
        with pytest.raises(BudgetExceeded, match="max_elapsed_seconds reached"):
            b.call_timeout()


def test_the_bounded_timeout_reaches_the_client(conn):
    run_id = db.create_run(conn, "q")["id"]
    mesh = FakeMesh(planner=[{"x": 1}])
    b, s = budget_at(70)
    call(LLM(mesh, s), conn, run_id, b)
    assert mesh.calls[0]["timeout"] == pytest.approx(30.0)  # not the 60 s default: only 30 s of the job are left
    mesh = FakeMesh(planner=[{"x": 1}])
    b, s = budget_at(5)
    call(LLM(mesh, s), conn, run_id, b)
    assert mesh.calls[0]["timeout"] == pytest.approx(60.0)


def test_no_request_is_started_or_counted_when_too_little_time_remains(conn):
    run_id = db.create_run(conn, "q")["id"]
    mesh = FakeMesh(planner=[{"x": 1}])
    b, s = budget_at(95)
    with pytest.raises(BudgetExceeded, match="insufficient time remaining"):
        call(LLM(mesh, s), conn, run_id, b)
    assert mesh.calls == [] and b.model_requests == 0 and b.output_tokens == 0  # nothing sent, nothing counted
    assert db.list_model_calls(conn, run_id) == []  # and nothing recorded as if it had been tried


def test_a_timed_out_request_counts_and_its_usage_stays_unknown(conn):
    run_id = db.create_run(conn, "q")["id"]
    mesh = FakeMesh(planner=[TIMEOUT])
    b, s = budget_at(10)
    with pytest.raises(ModelCallError, match="Request timed out"):
        call(LLM(mesh, s), conn, run_id, b)
    assert b.model_requests == 1  # the request was sent: it counts toward the request budget
    row = db.list_model_calls(conn, run_id)[0]
    assert row["ok"] is False and row["prompt_tokens"] is None and row["completion_tokens"] is None and row["total_tokens"] is None  # unknown stays unknown
    assert "request timeout 60s" in row["error"]
    assert b.output_tokens == s.max_output_tokens  # the CAP assumes the worst; the stored record invents nothing


def test_repeated_timeouts_use_up_the_request_budget_not_a_fresh_one(conn):
    run_id = db.create_run(conn, "q")["id"]
    s = Settings(mesh_api_key="", max_model_requests=3)
    b = Budget(s)
    mesh = FakeMesh(planner=[TIMEOUT])
    for _ in range(3):
        with pytest.raises(ModelCallError):
            call(LLM(mesh, s), conn, run_id, b)
    with pytest.raises(BudgetExceeded, match="max_model_requests"):
        call(LLM(mesh, s), conn, run_id, b)
    assert len(mesh.calls) == 3


# --- job level: drafting timeout, insufficient time, preserved evidence --------------------------------------------------------------------

def seeded(client):
    conn = db.connect(client.db_path)
    r = seed_collected_run(conn)
    conn.close()
    return r


def test_a_drafting_timeout_fails_the_job_honestly_and_keeps_the_collected_evidence(client, verify_env):
    r = seeded(client)
    mesh = FakeMesh(draft=[TIMEOUT])
    verify_env(mesh)
    assert client.post(f"/runs/{r['run']}/verify").status_code == 202
    v = client.get(f"/runs/{r['run']}/verification").json()
    assert v["verify_status"] == "failed" and "drafter failed" in v["verify_stop_reason"] and "Request timed out" in v["verify_stop_reason"]
    assert v["claims"] == 0 and len(mesh.calls) == 1  # one request, not retried by itself
    calls = client.get(f"/runs/{r['run']}/model-calls").json()
    assert len(calls) == 1 and calls[0]["ok"] is False and calls[0]["prompt_tokens"] is None
    # the evidence stays inspectable after the failure
    assert len(client.get(f"/runs/{r['run']}/sources").json()) == 1 and len(client.get(f"/runs/{r['run']}/passages").json()) == 4
    assert client.get(f"/runs/{r['run']}/claims").json() == []
    ev = next(e for e in client.get(f"/runs/{r['run']}/events").json() if e["event_type"] == "verification_finished")["payload"]
    assert ev["status"] == "failed" and ev["budget_used_of_limit"]["model_requests"][0] == 1  # the failed job's budget as it was used


def test_with_too_little_job_time_the_drafter_is_never_called_and_the_job_stops_honestly(client, verify_env):
    r = seeded(client)
    mesh = FakeMesh(draft=draft())
    verify_env(mesh, max_elapsed_seconds=5.0)  # less than min_call_seconds from the very start
    client.post(f"/runs/{r['run']}/verify")
    v = client.get(f"/runs/{r['run']}/verification").json()
    assert v["verify_status"] == "stopped" and "insufficient time remaining" in v["verify_stop_reason"]
    assert mesh.calls == [] and client.get(f"/runs/{r['run']}/model-calls").json() == []
    assert len(client.get(f"/runs/{r['run']}/sources").json()) == 1


# --- explicit retry from stored evidence ----------------------------------------------------------------------------------------------------

@pytest.fixture
def retry_env(client, fetcher, settings):
    def install(mesh, search, **overrides):
        s = settings.model_copy(update=overrides)
        svc = ResearchServices(s, LLM(mesh, s), search, fetcher, lambda: db.connect(client.db_path))
        app.dependency_overrides[get_retry_services] = lambda: svc
        return svc

    return install


def failed_completion(client, complete_env):
    """A run whose end-to-end completion failed in drafting (the observed failure), through the real API and graph."""
    r = seeded(client)
    complete_env(FakeMesh(draft=[TIMEOUT]), FakeSearch({}))
    assert client.post(f"/runs/{r['run']}/complete").status_code == 202
    run = client.get(f"/runs/{r['run']}").json()
    assert run["verify_status"] == "failed" and run["complete_status"] == "failed"
    return r


def good_replies():
    return {
        "draft": draft(claim(1, Q0, ("S1-P0", Q0)), claim(2, Q2, ("S1-P2", Q2))),
        "verify": [verdict("supported", used=["S1-P0"]), verdict("supported", used=["S1-P2"])],
        "obligations": obligations(Q1_R1=[("SQLite", "deployment model")], Q1_R2=[("SQLite", "release facts")], Q2_R1=[("PostgreSQL", "concurrency model")]),
        "coverage": [coverage(("Q1.R1.O1", "addressed", [1], "", "none"), ("Q1.R2.O1", "addressed", [1], "", "none"), ("Q2.R1.O1", "addressed", [2], "", "none"))],
        "synth": synth((1, [1]), (2, [2]))}


def test_the_retry_plan_shows_the_stored_evidence_the_new_limits_and_the_failed_jobs_budget(client, complete_env):
    r = failed_completion(client, complete_env)
    plan = client.get(f"/runs/{r['run']}/retry").json()
    assert plan["allowed"] is True and plan["reasons"] == [] and plan["attempt"] == 1
    assert plan["stored_evidence"] == {"sources": 1, "passages": 4, "claims": 0}
    assert plan["new_job_limits"]["search_queries"] == 0 and plan["new_job_limits"]["page_fetches"] == 0 and plan["new_job_limits"]["model_requests"] > 0
    assert "drafter failed" in plan["previous_job"]["stop_reason"] and plan["previous_job"]["budget_used_of_limit"]["model_requests"][0] == 1


def test_a_retry_never_happens_without_an_explicit_confirmation(client, complete_env, retry_env):
    r = failed_completion(client, complete_env)
    mesh = FakeMesh(**good_replies())
    retry_env(mesh, FakeSearch({}))
    assert client.post(f"/runs/{r['run']}/retry", json={}).status_code == 400
    assert client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": False}).status_code == 400
    assert mesh.calls == []  # nothing started
    run = client.get(f"/runs/{r['run']}").json()
    assert run["verify_status"] == "failed" and run["complete_status"] == "failed"  # unchanged: no silent reset
    assert not [e for e in client.get(f"/runs/{r['run']}/events").json() if e["event_type"] == "job_retry_started"]


def test_an_explicit_retry_continues_from_stored_evidence_without_any_retrieval(client, complete_env, retry_env, web):
    r = failed_completion(client, complete_env)
    before = client.get(f"/runs/{r['run']}/events").json()
    failed_budget = next(e for e in before if e["event_type"] == "completion_finished")["payload"]["budget_used_of_limit"]
    search = FakeSearch({})
    mesh = FakeMesh(**good_replies())
    retry_env(mesh, search)
    resp = client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": True})
    assert resp.status_code == 202 and resp.json()["attempt"] == 1 and resp.json()["retrieval"].startswith("disabled")

    done = client.get(f"/runs/{r['run']}/completion").json()
    assert done["complete_status"] == "completed" and done["report_outcome"] == "claims_all_requirements_addressed"
    # no retrieval of any kind: no search query, no page request, no query-writing or page-selecting model step
    assert search.queries == [] and web.requests == []
    assert set(mesh.steps()) <= {"draft", "verify", "coverage", "synth", "obligations"}
    # the stored evidence was used as it was: same single source, no new source
    assert len(client.get(f"/runs/{r['run']}/sources").json()) == 1
    # lineage: the retry event carries the failed job's final budget and reason, and the failed job's own record is untouched
    events = client.get(f"/runs/{r['run']}/events").json()
    retry = next(e for e in events if e["event_type"] == "job_retry_started")["payload"]
    assert retry["confirmed"] is True and retry["attempt"] == 1 and retry["retrieval"] == "disabled"
    assert retry["retry_of"]["budget_used_of_limit"] == failed_budget and "drafter failed" in retry["retry_of"]["stop_reason"]
    assert retry["new_job_limits"]["search_queries"] == 0 and retry["new_job_limits"]["page_fetches"] == 0
    old_finish = next(e for e in events if e["event_type"] == "completion_finished")
    assert old_finish["payload"]["budget_used_of_limit"] == failed_budget and old_finish["payload"]["status"] == "failed"
    # the new job has its OWN budget: its start record shows zeros and its final record counts only its own requests
    starts = [e for e in events if e["event_type"] == "verification_started"]
    assert starts[-1]["payload"]["limits"]["model_requests"][0] == 0 and starts[-1]["payload"]["limits"]["search_queries"] == [0, 0]
    new_finish = [e for e in events if e["event_type"] == "completion_finished"][-1]["payload"]["budget_used_of_limit"]
    assert new_finish["model_requests"][0] == len(mesh.calls) and new_finish["search_queries"] == [0, 0] and new_finish["page_fetches"] == [0, 0]
    # the run total counts BOTH jobs' paid requests
    assert client.get(f"/runs/{r['run']}/research").json()["model_usage"]["model_calls"] == 1 + len(mesh.calls)


def test_a_retry_is_refused_when_nothing_failed_when_running_or_without_evidence_or_with_claims(client, complete_env, retry_env):
    retry_env(FakeMesh(**good_replies()), FakeSearch({}))
    fresh = db.connect(client.db_path)
    created = db.create_run(fresh, "never collected")["id"]
    fresh.close()
    assert client.post(f"/runs/{created}/retry", json={"confirm_paid_retry": True}).status_code == 409  # no evidence, collection never finished
    r = seeded(client)  # collected but completion not started
    resp = client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": True})
    assert resp.status_code == 409 and "not been started" in resp.json()["detail"]
    ok = failed_completion(client, complete_env)
    c = db.connect(client.db_path)
    with db.tx(c):
        c.execute("UPDATE runs SET complete_status = 'completing' WHERE id = ?", (ok["run"],))
    c.close()
    resp = client.post(f"/runs/{ok['run']}/retry", json={"confirm_paid_retry": True})
    assert resp.status_code == 409 and "still running" in resp.json()["detail"]
    c = db.connect(client.db_path)
    with db.tx(c):
        c.execute("UPDATE runs SET complete_status = 'failed' WHERE id = ?", (ok["run"],))
        claims.add_claim(c, ok["run"], ok["sq1"], Q0, [(ok["p"](0), Q0)], origin="drafted")
    c.close()
    resp = client.post(f"/runs/{ok['run']}/retry", json={"confirm_paid_retry": True})
    assert resp.status_code == 409 and "claim(s) already exist" in resp.json()["detail"]


def test_a_second_retry_after_another_failure_is_a_new_attempt_with_its_own_lineage(client, complete_env, retry_env):
    r = failed_completion(client, complete_env)
    retry_env(FakeMesh(draft=[TIMEOUT]), FakeSearch({}))
    assert client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": True}).status_code == 202  # fails again, by itself
    assert client.get(f"/runs/{r['run']}").json()["complete_status"] == "failed"
    plan = client.get(f"/runs/{r['run']}/retry").json()
    assert plan["allowed"] and plan["attempt"] == 2  # a person must ask again; nothing retried on its own
    mesh = FakeMesh(**good_replies())
    retry_env(mesh, FakeSearch({}))
    resp = client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": True})
    assert resp.json()["attempt"] == 2
    retries = [e["payload"] for e in client.get(f"/runs/{r['run']}/events").json() if e["event_type"] == "job_retry_started"]
    assert [x["attempt"] for x in retries] == [1, 2]
    assert "drafter failed" in retries[1]["retry_of"]["stop_reason"] and retries[1]["retry_of"]["event_id"] != retries[0]["retry_of"]["event_id"]
    assert client.get(f"/runs/{r['run']}/completion").json()["complete_status"] == "completed"


# --- latency of FAILED attempts ------------------------------------------------------------------------------------------------------------

class SlowFailMesh(FakeMesh):
    """FIXTURE: advances a controlled clock by `wait` seconds while the request is 'in flight', then fails (or answers) as scripted."""

    def __init__(self, clock, wait, **replies):
        super().__init__(**replies)
        self.clock, self.wait = clock, wait

    def _create(self, **kwargs):
        self.clock["t"] += self.wait
        return super()._create(**kwargs)


@pytest.mark.parametrize("failure", [TIMEOUT, ConnectionError("connection reset")])
def test_a_failed_attempt_records_its_measured_wait_and_keeps_usage_and_cost_unknown(conn, monkeypatch, failure):
    clock = {"t": 1000.0}
    monkeypatch.setattr("backend.llm.time.monotonic", lambda: clock["t"])  # controlled clock: no real waiting, no wall-clock dependence
    run_id = db.create_run(conn, "q")["id"]
    b, s = budget_at(10)
    with pytest.raises(ModelCallError):
        call(LLM(SlowFailMesh(clock, 60.5, planner=[failure]), s), conn, run_id, b)
    row = db.list_model_calls(conn, run_id)[0]
    assert row["ok"] is False and row["latency_ms"] == 60500  # the wait is known exactly, even though nothing came back
    assert row["prompt_tokens"] is None and row["completion_tokens"] is None and row["total_tokens"] is None  # usage stays unknown
    assert row["finish_reason"] is None and row["request_id"] is None  # nothing was returned, so nothing is stored
    assert b.model_requests == 1


def test_latency_of_a_successful_attempt_is_unchanged(conn, monkeypatch):
    clock = {"t": 50.0}
    monkeypatch.setattr("backend.llm.time.monotonic", lambda: clock["t"])
    run_id = db.create_run(conn, "q")["id"]
    b, s = budget_at(10)
    call(LLM(SlowFailMesh(clock, 2.25, planner=[{"x": 1}]), s), conn, run_id, b)
    row = db.list_model_calls(conn, run_id)[0]
    assert row["ok"] is True and row["latency_ms"] == 2250 and row["prompt_tokens"] == 100  # fixture usage, reported by the fake provider


def test_a_failed_job_request_shows_its_wait_through_the_api(client, verify_env):
    r = seeded(client)
    verify_env(FakeMesh(draft=[TIMEOUT]))
    client.post(f"/runs/{r['run']}/verify")
    call_row = client.get(f"/runs/{r['run']}/model-calls").json()[0]
    assert call_row["ok"] is False and call_row["latency_ms"] is not None and call_row["latency_ms"] >= 0
    assert call_row["prompt_tokens"] is None and call_row["completion_tokens"] is None and call_row["finish_reason"] is None
