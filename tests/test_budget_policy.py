"""The request-budget policy (backend/budget_policy.py) in the REAL graph, end to end, OFFLINE: every model reply is a scripted fixture.

Reproduces the shape of Run 2 (27 Oct 2026): one 20-request job that plans, researches 4 subquestions (2 researcher requests each), checks relevance, drafts and
verifies, with two verifier replies rejected for validation (each costing a corrective retry). Before the policy that job spent all 20 requests before any
coverage assessment and its report said nothing was assessed. These tests check the request counter, the order of stages, what was held, and the stored
report. They do not show anything about research quality or answer accuracy.
"""

import pytest

from backend import budget_policy, graph
from backend.budget import Budget
from tests.fakes import (
    FakeMesh,
    FakeSearch,
    claim,
    coverage,
    draft,
    obligations,
    plan,
    queries,
    select,
    synth,
    verdict,
)

N = 4  # subquestions, as in Run 2
URL = {n: f"https://example.test/topic{n}" for n in range(1, N + 1)}
FACT = {(n, k): f"Topic {n} fact {k} is stated in this fixture page." for n in range(1, N + 1) for k in (1, 2)}
OPTIONAL = {"planner", "queries", "select", "relevance", "draft", "verify", "revise", "synth"}
FINAL = {"obligations", "coverage", "consistency"}


def pages(web):
    for n in range(1, N + 1):
        web.html(URL[n], f"<html><head><title>Topic {n}</title></head><body><p>{FACT[(n, 1)]} {FACT[(n, 2)]}</p></body></html>")


def run2_mesh(verify_replies, **extra):
    """Planner 1 + (queries + select) x 4 + relevance + drafter returning 8 claims (2 per subquestion) + the given verifier replies."""
    claims_ = [claim(n, FACT[(n, k)], (f"S{n}-P0", FACT[(n, k)])) for n in range(1, N + 1) for k in (1, 2)]
    replies = {"planner": plan(*[(f"What does fixture topic {n} say?", [f"topic {n} facts"]) for n in range(1, N + 1)]),
                   "queries": [queries(f"topic {n} query") for n in range(1, N + 1)], "select": [select(URL[n]) for n in range(1, N + 1)],
                   "draft": draft(*claims_), "verify": verify_replies, "obligations": obligations(),
                   "coverage": coverage(*[(f"Q{n}.R1", "addressed", [2 * n - 1], "", "none") for n in range(1, N + 1)]),
                   "synth": synth(*[(n, [2 * n - 1, 2 * n]) for n in range(1, N + 1)])}
    replies.update(extra)
    return FakeMesh(**replies)


def ok(n):
    return verdict("supported", used=[f"S{n}-P0"])


def sq_of(claim_index):  # claim 1, 2 -> subquestion 1; 3, 4 -> 2; ...
    return (claim_index + 1) // 2


def start(client, complete_env, web, mesh, **overrides):
    pages(web)
    complete_env(mesh, FakeSearch({f"topic {n} query": [(URL[n], f"Topic {n}", "s")] for n in range(1, N + 1)}), **overrides)
    run_id = client.post("/runs", json={"question": "What do the four fixture topics say?"}).json()["id"]
    assert client.post(f"/runs/{run_id}/research?complete=true").status_code == 202
    return run_id


def state(client, run_id):
    events = client.get(f"/runs/{run_id}/events").json()
    return {"verification": client.get(f"/runs/{run_id}/verification").json(), "completion": client.get(f"/runs/{run_id}/completion").json(),
            "report": client.get(f"/runs/{run_id}/report").json(), "claims": client.get(f"/runs/{run_id}/claims").json(), "events": events,
            "held": [e["payload"] for e in events if e["event_type"] == "budget_held"],
            "curtailed": [e["payload"] for e in events if e["event_type"] == "budget_curtailed"]}


def assert_honest(st):
    """Whatever was cut: nothing unverified is reported, every reported claim has a supported verdict, and the cap was never exceeded."""
    by_id = {c["id"]: c for c in st["claims"]}
    reported = {i["claim_id"] for s in st["report"]["sections"] for i in s["claims"] + s["context"]}
    assert all(by_id[i]["semantic_verdict"] == "supported" and by_id[i]["eligible_for_report"] for i in reported)
    used, limit = st["completion"]["budget_used_of_limit"]["model_requests"]
    assert used <= limit


# --- 1. Run 2's sequence --------------------------------------------------------------------------------------------------------------------------

# Claims are kept and verified one per subquestion first (sq1, sq2, sq3, sq4, then sq1 again). Two validation retries, on the 2nd and 4th verifications, as in Run 2.
RUN2_VERIFY = [ok(1), "invalid reply", ok(2), ok(3), "invalid reply", ok(4), ok(1)]


def test_run2_sequence_keeps_the_final_assessment_and_reports_the_rest_as_partial(client, complete_env, web):
    mesh = run2_mesh(RUN2_VERIFY)
    run_id = start(client, complete_env, web, mesh)
    st = state(client, run_id)
    steps = mesh.steps()
    assert len(steps) == 20 == st["completion"]["budget_used_of_limit"]["model_requests"][0]  # the total cap is unchanged and reached exactly
    assert steps[:10] == ["planner"] + ["queries", "select"] * N + ["relevance"]
    # the final assessment ran after verification, with the capacity held for it
    last_optional = max(i for i, s in enumerate(steps) if s in OPTIONAL)
    assert steps[last_optional + 1:] == ["obligations", "coverage", "consistency"]
    # drafting was capped to what could be verified: 20 - 11 used - 3 held = 6 verifications, minus 1 of retry slack = 5 claims (8 were returned)
    drafted = next(e for e in st["events"] if e["event_type"] == "claims_drafted")["payload"]["claim_ids"]
    assert len(drafted) == 5
    # two invalid replies cost two retries: one claim per subquestion verified (both halves of the question), the second sq1 claim left unverified
    by_id = {c["id"]: c for c in st["claims"]}
    assert [by_id[i]["semantic_verdict"] for i in drafted] == ["supported"] * 4 + [None]
    assert by_id[drafted[-1]]["status"] == "awaiting_verification"
    v = st["verification"]
    assert v["verify_status"] == "stopped" and "left unverified" in v["verify_stop_reason"] and "held for the final assessment" in v["verify_stop_reason"]
    rep = st["report"]
    assert rep["coverage_assessed"] is True and rep["unassessed_requirements"] == []  # coverage was assessed this time
    assert rep["consistency"]["status"] != "not_checked"  # and the consistency check got its held request
    assert rep["ordering"] == "deterministic" and "synth" not in steps  # synthesis is optional; nothing was left for it
    assert "partial result" in rep["stop_reason"] and "left unverified" in rep["stop_reason"]
    assert any(o["claim_id"] == drafted[-1] and "not eligible" in o["reason"] for o in rep["omissions"])
    assert [h["phase"] for h in st["held"]][:3] == ["collection", "collection", "drafting"] and st["held"][-1]["phase"] == "report"
    assert {c["stage"] for c in st["curtailed"]} == {"verification"}
    assert_honest(st)


def test_without_the_policy_the_same_job_exhausted_the_budget_in_verification(client, complete_env, web, monkeypatch):
    """The failure mode itself, reproduced by switching the holds off (hold = 0): verification eats the whole budget and nothing is assessed."""
    monkeypatch.setattr(budget_policy, "hold_for", lambda *a, **k: (0, "", 0.0))
    monkeypatch.setattr(budget_policy, "drafting_cap", lambda budget, settings: settings.max_claims)
    mesh = run2_mesh(RUN2_VERIFY)
    st = state(client, start(client, complete_env, web, mesh))
    assert len(mesh.steps()) == 20 and "coverage" not in mesh.steps()
    assert st["report"]["coverage_assessed"] is False and len(st["report"]["unassessed_requirements"]) == N
    assert "NOT ASSESSED" in st["report"]["markdown"] and "## Evidence requirements not assessed" in st["report"]["markdown"]
    assert st["report"]["unresolved_requirements"] == []  # an absent assessment is not reported as "unanswered"


# --- 2. failures in verification ------------------------------------------------------------------------------------------------------------------

def test_a_transport_failure_in_verification_is_counted_and_never_supported(client, complete_env, web):
    mesh = run2_mesh([ok(1), RuntimeError("upstream 503"), ok(3), ok(4), ok(1)])
    st = state(client, start(client, complete_env, web, mesh))
    failed = [c for c in st["claims"] if c["semantic_verdict"] == "error"]
    assert len(failed) == 1 and failed[0]["eligible_for_report"] is False and failed[0]["status"] == "verification_error"
    assert {"obligations", "coverage", "consistency"} <= set(mesh.steps())
    assert_honest(st)


# --- 3. more drafted claims than verification capacity ----------------------------------------------------------------------------------------------

def test_drafting_is_capped_to_the_claims_that_can_still_be_verified(client, complete_env, web):
    mesh = run2_mesh([ok(1), ok(2), ok(3), ok(4), ok(1)])
    st = state(client, start(client, complete_env, web, mesh))
    drafted = next(e for e in st["events"] if e["event_type"] == "claims_drafted")["payload"]["claim_ids"]
    assert len(drafted) == 5  # 8 returned by the drafter; 6 verifications affordable, 1 kept as slack
    assert all(c["semantic_verdict"] == "supported" for c in st["claims"])  # every drafted claim was verified
    assert st["verification"]["verify_status"] == "completed" and st["curtailed"] == []
    steps = mesh.steps()
    assert steps.count("verify") == 5 and steps[-4:] == ["obligations", "coverage", "consistency", "synth"]  # one request was left for synthesis
    assert st["report"]["ordering"] == "model"
    assert_honest(st)


def test_the_drafting_cap_arithmetic():
    from backend.config import Settings
    s = Settings(_env_file=None, mesh_api_key="", max_model_requests=20, max_claims=8)
    b = Budget(s)
    b.model_requests = 10  # Run 2 before drafting: planner 1, researcher 8, relevance 1
    b.hold(3, "the final assessment")
    assert b.optional_left() == 7 and budget_policy.drafting_cap(b, s) == 5  # 1 to draft, 6 verifications, 1 of them kept as slack
    b.model_requests = 15
    assert budget_policy.drafting_cap(b, s) == 1  # 2 optional: 1 to draft, 1 verification, no room for slack
    b.model_requests = 16
    assert budget_policy.drafting_cap(b, s) == 0  # drafting alone would leave nothing to verify with: do not draft
    big = Budget(s)
    assert budget_policy.drafting_cap(big, s) == s.max_claims  # plenty: the usual limit applies


# --- 4. the correction loop near exhaustion: a sweep over request limits ----------------------------------------------------------------------------

import itertools

from tests.test_phase4_loop import happy_mesh, run_complete, world  # noqa: F401  (world is a fixture)

CORRECTION_WORK = {"revise", "queries", "select", "draft", "verify"}


@pytest.mark.parametrize("cap", range(3, 17))
def test_corrections_never_consume_the_final_assessment_at_any_request_limit(client, complete_env, world, cap):  # noqa: F811
    r, _ = world
    mesh = happy_mesh(r)
    run_complete(client, complete_env, mesh, max_model_requests=cap)
    steps = mesh.steps()
    assert len(steps) <= cap
    first = steps.index("coverage")
    work = [i for i, x in enumerate(steps) if i > first and x in CORRECTION_WORK]
    if work:  # whatever corrections ran, a coverage assessment of their result followed: they could not use its request
        assert "coverage" in steps[max(work) + 1:]
    covers = [i for i, x in enumerate(steps) if x == "coverage"]
    for a, b in itertools.pairwise(covers):  # never two assessments of an unchanged claim set back to back
        assert any(x in CORRECTION_WORK for x in steps[a + 1:b])
    eligible = [c for c in client.get("/runs/1/claims").json() if c["eligible_for_report"]]
    rep = client.get("/runs/1/report").json()
    if len(eligible) >= 2:  # the consistency check always got the request held for it
        assert "consistency" in steps and rep["consistency"]["status"] != "not_checked"
    assert rep["coverage_assessed"] is True


# --- 5. limits the hold does not cover: output tokens and time ------------------------------------------------------------------------------------

def test_running_out_of_output_tokens_before_the_final_assessment_is_reported_as_not_assessed(client, complete_env, web):
    mesh = run2_mesh([ok(1), ok(2), ok(3), ok(4), ok(1)])
    mesh.completion_tokens["verify"] = 4000  # the request hold does not protect tokens: they run out during verification
    st = state(client, start(client, complete_env, web, mesh))
    assert "coverage" not in mesh.steps()
    assert "max_total_output_tokens" in st["completion"]["stop_reason"] and st["completion"]["complete_status"] == "stopped"
    rep = st["report"]
    assert rep["coverage_assessed"] is False and len(rep["unassessed_requirements"]) == N and rep["unresolved_requirements"] == []
    assert rep["consistency"]["status"] == "not_checked" and "NOT ASSESSED" in rep["markdown"]
    assert_honest(st)


def test_slow_optional_calls_cannot_use_the_time_held_for_the_final_assessment(client, complete_env, web, monkeypatch):
    """Every request "takes" 28 s against the 300 s job deadline. 45 s (3 final-assessment requests x 15 s) are held from optional work, so research is cut
    short of drafting: no claim can be drafted and verified in time, but the final assessment still runs inside the held time and the report says so."""
    class Clocked(Budget):
        def __init__(self, settings):
            super().__init__(settings, clock=lambda: 28.0 * getattr(self, "model_requests", 0))
    monkeypatch.setattr(graph, "Budget", Clocked)
    mesh = run2_mesh([ok(1), ok(2), ok(3), ok(4), ok(1)])
    st = state(client, start(client, complete_env, web, mesh, max_elapsed_seconds=300.0))
    steps = mesh.steps()
    assert "verify" not in steps and steps[-1] == "obligations"  # the final assessment ran in the held time (with no claims, coverage needs no model call)
    assert {c["stage"] for c in st["curtailed"]} >= {"relevance", "drafting"}  # research fitted; relevance and drafting would have run into the held time
    assert all(c["semantic_verdict"] is None for c in st["claims"])  # nothing unchecked is called supported
    rep = st["report"]
    assert rep["coverage_assessed"] is True and rep["outcome"] == "insufficient_evidence"
    assert "partial result" in (rep["stop_reason"] or "")
    assert_honest(st)


def test_running_out_of_time_with_no_hold_is_reported_as_not_assessed(client, complete_env, web, monkeypatch):
    """Time the hold cannot cover (the deadline passes during optional work that started legitimately): the job stops and the report says not assessed."""
    class Clocked(Budget):
        def __init__(self, settings):
            super().__init__(settings, clock=lambda: 28.0 * getattr(self, "model_requests", 0))
    monkeypatch.setattr(graph, "Budget", Clocked)
    monkeypatch.setattr(budget_policy, "FINAL_CALL_SECONDS", 0.0)
    mesh = run2_mesh([ok(1), ok(2), ok(3), ok(4), ok(1)])
    st = state(client, start(client, complete_env, web, mesh, max_elapsed_seconds=300.0))
    assert "coverage" not in mesh.steps() and "max_elapsed_seconds" in st["completion"]["stop_reason"]
    rep = st["report"]
    assert rep["coverage_assessed"] is False and len(rep["unassessed_requirements"]) == N
    assert_honest(st)


# --- 6. a complete path that fits ---------------------------------------------------------------------------------------------------------------

def test_a_job_that_fits_completes_every_stage_with_nothing_curtailed(client, complete_env, web):
    two = 2
    mesh = run2_mesh(
        [ok(1), ok(1), ok(2), ok(2)],  # 4 claims fit the cap: nothing is cut, so the drafter's order (sq1, sq1, sq2, sq2) is kept
        planner=plan(*[(f"What does fixture topic {n} say?", [f"topic {n} facts"]) for n in range(1, two + 1)]),
        queries=[queries(f"topic {n} query") for n in range(1, two + 1)], select=[select(URL[n]) for n in range(1, two + 1)],
        draft=draft(*[claim(n, FACT[(n, k)], (f"S{n}-P0", FACT[(n, k)])) for n in range(1, two + 1) for k in (1, 2)]),
        coverage=coverage(("Q1.R1", "addressed", [1, 2], "", "none"), ("Q2.R1", "addressed", [3, 4], "", "none")),
        consistency={"claims_reviewed": [1, 2, 3, 4], "findings": []}, synth=synth((1, [1, 2]), (2, [3, 4])))
    st = state(client, start(client, complete_env, web, mesh))
    steps = mesh.steps()
    assert steps == ["planner"] + ["queries", "select"] * two + ["relevance", "draft"] + ["verify"] * 4 + ["obligations", "coverage", "consistency", "synth"]
    assert st["curtailed"] == [] and st["verification"]["verify_status"] == "completed"
    done = st["completion"]
    assert done["complete_status"] == "completed" and done["report_outcome"] == "claims_all_requirements_addressed"
    rep = st["report"]
    assert rep["coverage_assessed"] is True and rep["unassessed_requirements"] == [] and rep["consistency"]["status"] == "reviewed" and rep["ordering"] == "model"
    assert "partial result" not in (rep["stop_reason"] or "")
    assert_honest(st)


# --- 7. time holds, slow calls and cancellation deadlines ---------------------------------------------------------------------------------------------

def test_an_optional_call_deadline_ends_where_the_held_time_begins_and_required_calls_get_the_rest():
    from backend.budget import ReserveReached
    from backend.config import Settings
    s = Settings(_env_file=None, mesh_api_key="", max_elapsed_seconds=300.0, min_call_seconds=10.0, mesh_timeout=90.0)
    now = [0.0]
    b = Budget(s, clock=lambda: now[0])
    b.hold(3, "the final assessment", seconds=45.0)
    now[0] = 200.0  # 100 s left: an optional call may run 55 s (it is cancelled at that deadline), a required call the full 90 s
    assert b.call_timeout() == 55.0
    with b.required():
        assert b.call_timeout() == 90.0
    now[0] = 250.0  # 50 s left, 45 held: an optional call would get 5 s, less than the 10 s minimum, so it is not started at all
    with pytest.raises(ReserveReached, match="45s are held for the final assessment"):
        b.call_timeout()
    with b.required():
        assert b.call_timeout() == 50.0
    assert b.model_requests == 0  # refusing a request charges nothing


# --- 8. a malformed reply should not destroy a valid result ---------------------------------------------------------------------------------------------

def test_a_valid_verdict_with_an_overlong_explanation_costs_no_retry_and_a_malformed_one_still_does(client, complete_env, web):
    long_ok = verdict("supported", explanation="S1-P0 states this. " + "x" * 700, used=["S1-P0"])  # 700+ chars: Run 2's retries were of this kind
    too_long = verdict("supported", explanation="y" * 1200, used=["S2-P0"])  # beyond the 1,000-character bound: rejected, one corrective retry
    mesh = run2_mesh([long_ok, too_long, ok(2), ok(3), ok(4), ok(1)])
    st = state(client, start(client, complete_env, web, mesh))
    calls = client.get("/runs/1/model-calls").json()
    verifier = [c for c in calls if c["agent"] == "verifier"]
    assert [c["ok"] for c in verifier[:3]] == [True, False, True]  # only the 1,200-character reply was rejected
    by_id = {c["id"]: c for c in st["claims"]}
    assert by_id[1]["semantic_verdict"] == "supported" and by_id[1]["semantic"]["reason"].startswith("S1-P0 states this.")


def test_run2_recorded_drafter_order_keeps_every_subquestion_under_the_cap():
    from backend.agents.drafter import fair_take
    recorded = [5, 5, 5, 6, 6, 7, 7, 8]  # Run 2's drafter output, claims 13-20, by subquestion (5/6: effect of merge/rebase; 7/8: when to choose each)
    assert recorded[:5] == [5, 5, 5, 6, 6]  # the old positional cut: no "when to choose" claim at all
    assert fair_take(recorded, 5, key=lambda x: x) == [5, 6, 7, 8, 5]  # one per subquestion first, then the next
    assert fair_take(recorded, 3, key=lambda x: x) == [5, 6, 7]
    assert fair_take(recorded, 8, key=lambda x: x) == recorded  # nothing to cut: the drafter's order is kept


def test_a_required_stage_keeps_the_next_required_stages_request_and_time():
    from backend.budget import ReserveReached
    from backend.config import Settings
    s = Settings(_env_file=None, mesh_api_key="", max_model_requests=20, max_elapsed_seconds=300.0, min_call_seconds=10.0, mesh_timeout=90.0)
    now = [0.0]
    b = Budget(s, clock=lambda: now[0])
    b.model_requests = 18  # two left: one for coverage, one kept for the consistency check
    with b.required(keep=1, keep_seconds=15.0, keep_for="the consistency check"):
        b.charge_model_request()  # coverage's first attempt
        with pytest.raises(ReserveReached, match="1 are kept for the consistency check"):
            b.charge_model_request()  # its retry would take the check's request
    assert b.model_requests == 19
    with b.required():
        b.charge_model_request()  # the consistency check itself
    assert b.model_requests == 20
    t = Budget(s, clock=lambda: now[0])
    now[0] = 270.0  # 30 s left
    with t.required(keep=1, keep_seconds=15.0, keep_for="the consistency check"):
        assert t.call_timeout() == 15.0  # coverage may run 15 s; the last 15 s are the check's
    now[0] = 280.0
    with t.required(keep=1, keep_seconds=15.0, keep_for="the consistency check"), pytest.raises(ReserveReached, match="15s are held for the consistency check"):
        t.call_timeout()
    with t.required():
        assert t.call_timeout() == 20.0
