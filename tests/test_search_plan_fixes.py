"""Targeted tests for: the corrective search reserve, optional association failures, plan coverage of the question, original-versus-revision wording,
coverage explanations that cite claims, and failed-run reporting. Scripted fixtures only: they pin deterministic behaviour and prompt text; none shows
that a model follows the prompts."""

import sqlite3

import pytest

from backend import db
from backend.agents import coverage, drafter, planner, verifier
from backend.budget import Budget, BudgetExceeded
from backend.config import Settings
from backend.llm import LLM, AgentOutputError
from evaluation import e2e_eval, e2e_review
from evaluation import metrics as m
from tests.conftest import A, B
from tests.fakes import FakeMesh, FakeSearch, plan, queries, select

Q02 = "When were HTTP/2 and HTTP/3 published as RFCs, and how do their transport layers differ?"
Q_HOL = "When were HTTP/2 and HTTP/3 published as RFCs, how do their transport layers differ, and how does each handle head-of-line blocking?"


def start(client, question="How big is it and who made it?"):
    run_id = client.post("/runs", json={"question": question}).json()["id"]
    return run_id, client.post(f"/runs/{run_id}/research")


def events(client, run_id, kind):
    conn = db.connect(client.db_path)
    try:
        return [e for e in db.list_events(conn, run_id) if e["event_type"] == kind]
    finally:
        conn.close()


# --- 1. search allocation ---------------------------------------------------------------------------------------------------------------

def test_the_reserve_is_part_of_the_unchanged_total_and_initial_collection_cannot_use_it():
    b = Budget(Settings(_env_file=None, mesh_api_key="", max_search_queries=8, corrective_search_reserve=2))
    assert (b.initial_cap(), b.corrective_reserve(), b.settings.max_search_queries) == (6, 2, 8)
    for _ in range(6):
        b.charge_search("initial")
    with pytest.raises(BudgetExceeded, match=r"initial search allocation exhausted \(6 of 6\); 2 of 8 searches are reserved"):
        b.charge_search("initial")
    b.charge_search("corrective")
    b.charge_search("corrective")
    with pytest.raises(BudgetExceeded, match=r"max_search_queries reached \(8\)"):
        b.charge_search("corrective")
    assert b.search_allocation()["total"] == [8, 8] and b.search_allocation()["initial"] == [6, 6] and b.search_allocation()["corrective"] == [2, 2]


def test_corrective_research_may_use_initial_allocation_that_collection_did_not_need():
    b = Budget(Settings(_env_file=None, mesh_api_key="", max_search_queries=8, corrective_search_reserve=2))
    for _ in range(3):
        b.charge_search("initial")
    for _ in range(5):
        b.charge_search("corrective")  # 3 unused initial + 2 reserved
    assert b.search_queries == 8


@pytest.mark.parametrize("total,reserve,expected", [(8, 2, 2), (8, 0, 0), (2, 5, 1), (1, 2, 0), (0, 2, 0)])
def test_the_reserve_never_exceeds_half_of_the_total_and_total_is_never_raised(total, reserve, expected):
    b = Budget(Settings(_env_file=None, mesh_api_key="", max_search_queries=total, corrective_search_reserve=reserve))
    assert b.corrective_reserve() == expected and b.initial_cap() + b.corrective_reserve() == total


def test_collection_with_four_subquestions_leaves_the_reserved_searches_for_correction(client, research):
    four = plan(("What is part one?", ["a"]), ("What is part two?", ["b"]), ("What is part three?", ["c"]), ("What is part four?", ["d"]))
    mesh = FakeMesh(planner=four, queries=[queries("alpha one", "alpha two"), queries("beta one", "beta two"), queries("gamma one", "gamma two"), queries("delta one", "delta two")], select=select(A))
    hits = {f"{w} {n}": [(A, "Alpha", "snippet")] for w in ("alpha", "beta", "gamma", "delta") for n in ("one", "two")}
    svc = research(mesh, FakeSearch(hits), max_search_queries=8, corrective_search_reserve=2)
    run_id, _ = start(client)
    conn = db.connect(client.db_path)
    searches = db.list_searches(conn, run_id)
    sq_with_search = {s["subquestion_id"] for s in searches}
    assert len(searches) == 6 and svc.settings.max_search_queries == 8  # four subquestions wanted 8; collection was held to 6
    assert len(sq_with_search) == 4  # every subquestion got a turn (fair share), none was starved by an earlier one
    started = events(client, run_id, "research_started")[0]["payload"]["search_allocation"]
    finished = events(client, run_id, "research_finished")[0]["payload"]["search_allocation"]
    assert started["initial"] == [0, 6] and started["corrective"] == [0, 2] and started["total"] == [0, 8]
    assert finished["initial"] == [6, 6] and finished["corrective"] == [0, 2] and finished["total"] == [6, 8]
    conn.close()


# --- 2. optional association failure ----------------------------------------------------------------------------------------------------

def _collect(client, research, **overrides):
    # subquestion 2 shares content words with page A (collected for subquestion 1), so the association step has a candidate and calls the model
    mesh = FakeMesh(planner=plan(("What is the alpha size?", ["a number"]), ("Which alpha paragraph holds a distinct statement?", ["alpha paragraph statement"])),
                    queries=[queries("size query"), queries("maker query")], select=[select(A), select(B)], relevance=[RuntimeError("provider unreachable")])
    research(mesh, FakeSearch({"size query": [(A, "Alpha", "s")], "maker query": [(B, "Beta", "b")]}), **overrides)
    return start(client), mesh


def test_a_failed_association_call_is_a_warning_and_the_direct_links_survive(client, research):
    (run_id, _), mesh = _collect(client, research)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "collection_finished" and status["stop_reason"] is None
    assert status["subquestions_by_status"] == {"evidence_collected": 2}  # the sources collected for each subquestion are still linked to it
    (warning,) = events(client, run_id, "association_failed")
    assert "direct source-subquestion links were kept" in warning["payload"]["warning"] and warning["payload"]["error_type"] == "ModelCallError"
    assert mesh.steps().count("relevance") == 1


def test_the_run_continues_only_while_the_shared_deadline_and_budget_permit(client, research, monkeypatch):
    from backend import budget as budget_mod
    failed = {"yes": False}
    original = budget_mod.Budget.call_timeout

    def call_timeout(self):
        if failed["yes"]:
            raise BudgetExceeded("max_elapsed_seconds reached (test: the deadline passed during the failed call)")
        return original(self)

    class HookMesh(FakeMesh):
        def _create(self, **kw):
            if "running a relevance check" in kw["messages"][0]["content"]:
                failed["yes"] = True
            return super()._create(**kw)

    monkeypatch.setattr(budget_mod.Budget, "call_timeout", call_timeout)
    mesh = HookMesh(planner=plan(("What is the alpha size?", ["a number"]), ("Which alpha paragraph holds a distinct statement?", ["alpha paragraph statement"])),
                    queries=[queries("size query"), queries("maker query")], select=[select(A), select(B)], relevance=[RuntimeError("provider unreachable")])
    research(mesh, FakeSearch({"size query": [(A, "Alpha", "s")], "maker query": [(B, "Beta", "b")]}))
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "stopped" and "max_elapsed_seconds reached" in status["stop_reason"]
    assert len(events(client, run_id, "association_failed")) == 1  # recorded, then the job stopped honestly


@pytest.mark.parametrize("error", [sqlite3.IntegrityError("FOREIGN KEY constraint failed"), ValueError("a programming error")])
def test_database_integrity_and_programming_errors_are_not_suppressed(client, research, monkeypatch, error):
    from backend import graph

    def broken(*a, **k):
        raise error

    monkeypatch.setattr(graph, "associate_sources", broken)
    (run_id, _), _ = _collect(client, research)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "failed" and f"unexpected {type(error).__name__}" in status["stop_reason"]
    assert events(client, run_id, "association_failed") == []


# --- 3. planning covers the whole question; original versus revision --------------------------------------------------------------------

def test_clauses_of_the_real_q02_question_and_of_a_three_part_question():
    spans = planner.question_clauses(Q02)
    assert [Q02[s:e].strip() for s, e in spans] == ["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ?"]
    assert len(planner.question_clauses(Q_HOL)) == 3 and len(planner.question_clauses("What is SQLite?")) == 1


def _p(question, asks, covers, n=None):
    return planner.Plan.model_validate({"asks": [{"quote": q, "label": q} for q in asks], "subquestions": [
        {"question": f"subquestion number {i}", "evidence_requirements": ["x"], "rationale": "r", "covers": c} for i, c in enumerate(covers)]})


def test_a_plan_that_drops_the_head_of_line_blocking_part_is_rejected():
    dropped = _p(Q_HOL, ["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ"], [[0], [1]])
    problems = planner.plan_problems(Q_HOL, dropped)
    assert len(problems) == 1 and "head-of-line blocking" in problems[0]
    full = _p(Q_HOL, ["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ", "how does each handle head-of-line blocking"],
              [[0], [1], [2]])
    assert planner.plan_problems(Q_HOL, full) == []


def test_the_real_q02_question_needs_both_of_its_parts_and_every_ask_needs_a_subquestion():
    assert planner.plan_problems(Q02, _p(Q02, ["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ"], [[0], [1]])) == []
    only_dates = planner.plan_problems(Q02, _p(Q02, ["When were HTTP/2 and HTTP/3 published as RFCs"], [[0]]))
    assert any("transport layers differ" in p for p in only_dates)
    uncovered = planner.plan_problems(Q02, _p(Q02, ["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ"], [[0]]))
    assert any("covered by no subquestion" in p for p in uncovered)
    invented = planner.plan_problems(Q02, _p(Q02, ["When were HTTP/2 and HTTP/3 published as RFCs", "words that are not in the question"], [[0], [1]]))
    assert any("not in the question" in p for p in invented) and any("transport layers" in p for p in invented)
    assert any("do not exist" in p for p in planner.plan_problems(Q02, _p(Q02, ["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ"], [[0], [7]])))


def _plan_dict(asks, covers):
    return {"asks": [{"quote": q, "label": q} for q in asks], "subquestions": [
        {"question": f"subquestion number {i}", "evidence_requirements": ["x"], "rationale": "r", "covers": c} for i, c in enumerate(covers)]}


def test_run_planner_repairs_a_plan_once_and_refuses_one_that_still_drops_part_of_the_question(conn, settings):
    run = db.create_run(conn, Q_HOL)
    bad = _plan_dict(["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ"], [[0], [1]])
    good = _plan_dict(["When were HTTP/2 and HTTP/3 published as RFCs", "how do their transport layers differ", "how does each handle head-of-line blocking"], [[0], [1], [2]])
    mesh = FakeMesh(planner=[bad, good])
    ids = planner.run_planner(conn, LLM(mesh, settings), Budget(settings), settings, run)
    assert len(ids) == 3 and mesh.steps() == ["planner", "planner"]
    assert "Your previous plan was rejected" in mesh.calls[1]["messages"][1]["content"] and "head-of-line blocking" in mesh.calls[1]["messages"][1]["content"]
    assert [e["event_type"] for e in db.list_events(conn, run["id"]) if e["event_type"] == "plan_rejected"] == ["plan_rejected"]
    run2 = db.create_run(conn, Q_HOL)
    with pytest.raises(AgentOutputError, match="still does not cover"):
        planner.run_planner(conn, LLM(FakeMesh(planner=[bad, bad]), settings), Budget(settings), settings, run2)
    assert db.list_subquestions(conn, run2["id"]) == []  # nothing is stored for a plan that was refused


def test_the_prompts_separate_original_publication_from_later_revisions_without_naming_any_fact():
    assert "ORIGINAL publication" in planner.SYSTEM and "A revision's date is not the original date" in planner.SYSTEM
    assert "never present its date as the original publication" in drafter.SYSTEM and "obsoletes" in drafter.SYSTEM
    assert "Original versus later revision" in verifier.SYSTEM and "mark date as mismatch" in verifier.SYSTEM
    for prompt in (planner.SYSTEM, drafter.SYSTEM, verifier.SYSTEM):
        for fact in ("9113", "7540", "9114", "HTTP/2", "HTTP/3", "RFC"):
            assert fact not in prompt, f"{fact!r} is hard-coded in a prompt"


# --- coverage explanations that cite claims ---------------------------------------------------------------------------------------------

ELIGIBLE = {2: {"text": "HTTP/3 was published as a Proposed Standard in RFC 9114 on 6 June 2022."}, 3: {"text": "HTTP/2 uses TCP as its transport layer protocol."}}


@pytest.mark.parametrize("text", [
    "HTTP/3 transport protocol name is mentioned in claim 3 (QUIC) but the obligation requires the name of the transport protocol used by HTTP/3",
    "Claim 3 mentions HTTP/3 uses QUIC but does not confirm whether QUIC is based on UDP",
    "claim 9 supports it",
])
def test_explanations_whose_cited_claims_do_not_contain_what_is_attributed_to_them_are_rejected(text):
    assert coverage.unsupported_claim_references(text, ELIGIBLE)


def test_an_explanation_that_matches_its_cited_claim_is_kept():
    assert coverage.unsupported_claim_references("claim 2 gives RFC 9114 and 6 June 2022 but not the transport protocol", ELIGIBLE) == []
    assert coverage.unsupported_claim_references("no cited claim names the protocol", ELIGIBLE) == []


# --- 4. failed runs in the evaluation ---------------------------------------------------------------------------------------------------

def _failed_run(conn, question, *, latency_ms, error, reason):
    run = db.create_run(conn, question)
    with db.tx(conn):
        db.claim_run_for_research(conn, run["id"])
        db.record_model_call(conn, run["id"], agent="relevance", model="m", attempt=1, ok=False, latency_ms=latency_ms, error=error)
        db.set_run_status(conn, run["id"], "failed", reason)
    return run["id"]


TIMEOUT = "ModelCallError: TimeoutError: wall-clock deadline of 60s reached; the request was cancelled and its connection closed (request timeout 60s)"


@pytest.mark.parametrize("latency,error,reason,expected", [
    (895750, TIMEOUT, "relevance check failed: " + TIMEOUT, "environment_interrupted_suspected"),
    (60400, TIMEOUT, "relevance check failed: " + TIMEOUT, "transport_timeout"),
    (900, "invalid output: 1 validation error", "drafter failed: x", "validation_failure"),
    (None, None, "max_model_requests reached (40)", "budget_exhaustion"),
])
def test_failure_info_classifies_a_failed_run_from_its_stored_records(conn, latency, error, reason, expected):
    rid = _failed_run(conn, "q", latency_ms=latency, error=error, reason=reason) if error else None
    if rid is None:
        run = db.create_run(conn, "q")
        with db.tx(conn):
            db.set_run_status(conn, run["id"], "failed", reason)
        rid = run["id"]
    info = e2e_eval.failure_info(conn, rid)
    assert info["classification"] == expected and info["report_stored"] is False
    if "relevance" in reason:
        assert info["stage"].startswith("collection: source-relevance check")


def test_a_failed_run_is_counted_as_a_failure_and_never_as_an_abstention_or_an_empty_answer():
    rows = [{"id": "Q02", "expected": "answerable", "report_claims": 3, "report_outcome": "claims_with_gaps", "failed": False},
            {"id": "Q05", "expected": "evidence_seeking", "report_claims": 0, "report_outcome": None, "failed": True, "failure_class": "environment_interrupted_suspected",
             "seeking_gold": "honest_not_found"},
            {"id": "QX", "expected": "answerable", "report_claims": 0, "report_outcome": None, "failed": True, "failure_class": "validation_failure"}]
    out = m.abstention_and_retention(rows)
    assert out["run_completion"]["failed_runs"] == 2 and out["run_completion"]["completed_with_report"]["numerator"] == 1
    assert out["run_completion"]["failure_classes"] == {"environment_interrupted_suspected": 1, "validation_failure": 1}
    assert out["evidence_seeking_judged_by_human"] == 0 and out["evidence_seeking_acceptable"]["status"] == "N/A"  # even a (mistaken) label cannot make it an abstention
    assert out["answerable_with_no_report_claims"]["numerator"] == 0  # the failed answerable run is a failure, not an empty answer


def test_the_requirements_sheet_shows_the_failure_and_its_stage(tmp_path):
    path = tmp_path / "e.db"
    conn = db.connect(path)
    rid = _failed_run(conn, "Has Mistral AI publicly reported a net profit or loss for fiscal year 2024, and if so, what is the figure and which primary source states it?",
                      latency_ms=895750, error=TIMEOUT, reason="relevance check failed: " + TIMEOUT)
    conn.close()
    notes = {f"Q05@{rid}": {"classification": "environment_interrupted", "evidence": "host in Modern Standby"}}
    e2e_review.build(path, [("Q05", rid)], tmp_path / "s", notes)
    md = (tmp_path / "s_requirements.md").read_text(encoding="utf-8")
    assert "THIS RUN FAILED" in md and "collection: source-relevance check" in md and "environment_interrupted" in md and "not an abstention" in md
    rows = (tmp_path / "s_requirements.csv").read_text(encoding="utf-8").splitlines()
    assert all("RUN FAILED at collection: source-relevance check" in r and "environment_interrupted" in r for r in rows[1:]) and len(rows) == 4
