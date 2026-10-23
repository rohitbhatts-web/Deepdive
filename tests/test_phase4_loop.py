"""Phase 4 ORCHESTRATION tests: the real LangGraph loop (coverage -> correction -> re-verification -> coverage -> report).
Model replies are SCRIPTED fixtures: these tests prove routing, budgets, versioning and report invariants. They say
nothing about research quality or about whether a real model would make these assessments."""

import pytest

from backend import claims, db
from backend.budget import Budget
from backend.llm import LLM
from tests.conftest import A, B
from tests.fakes import (
    FakeMesh,
    FakeSearch,
    claim,
    coverage,
    draft,
    queries,
    revise,
    select,
    split,
    synth,
    verdict,
)
from tests.loop_helpers import CONCURRENCY, SENTINEL, SINGLE_FILE, seed_mixed
from tests.seed import PARAS, Q0

RELEASE = "Version 3.45 was released in January 2024 according to the official release notes."
MANUAL = "The PostgreSQL manual states that readers do not block writers under multiversion concurrency control."
B_NEW = "The SQLite database is a single file on disk."
C_NEW = "Postgres can handle hundreds of concurrent clients, though results may vary."
N1 = "Version 3.45 was released in January 2024."
N2 = "Readers do not block writers under multiversion concurrency control."
Q_REL, Q_PG = "sqlite release date 3.45 notes", "postgresql multiversion concurrency manual"
GAP_REL = "the release date of the version is not established"
GAP_PG = "no verified claim about how concurrent readers and writers behave"


@pytest.fixture
def world(client, web, settings):
    conn = db.connect(client.db_path)
    r, ids = seed_mixed(conn, settings)
    conn.close()
    web.html(A, f"<html><head><title>Release notes</title></head><body><p>{RELEASE}</p></body></html>")
    web.html(B, f"<html><head><title>PG manual</title></head><body><p>{MANUAL}</p></body></html>")
    return r, ids


def happy_mesh(r, **replace):
    p = r["p"]
    replies = {
        "coverage": [
            coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "unresolved", [], GAP_REL, "date"),
                     ("Q2.R1", "unresolved", [], GAP_PG, "primary_documentation")),
            coverage(("Q1.R1", "addressed", [1, 6], "", "none"), ("Q1.R2", "addressed", [8], "", "none"),
                     ("Q2.R1", "addressed", [7, 9], "", "none"))],
        "revise": [revise(B_NEW, (p(0), SINGLE_FILE)), revise(C_NEW, (p(2), CONCURRENCY))],
        "queries": [queries(Q_REL), queries(Q_PG)],
        "select": [select(A), select(B)],
        "draft": draft(claim(1, N1, ("S2-P0", "Version 3.45 was released in January 2024")),
                       claim(2, N2, ("S3-P0", "readers do not block writers under multiversion concurrency control"))),
        "verify": [verdict("supported", used=[p(0)]), verdict("supported", used=[p(2)]),
                   verdict("supported", used=["S2-P0"]), verdict("supported", used=["S3-P0"])],
        "synth": synth((1, [1, 6, 8]), (2, [7, 9])),
    }
    replies.update(replace)
    return FakeMesh(**replies)


def search():
    return FakeSearch({Q_REL: [(A, "Release notes", "snippet")], Q_PG: [(B, "PG manual", "snippet")]})


def run_complete(client, complete_env, mesh, srch=None, **overrides):
    complete_env(mesh, srch or search(), **overrides)
    return client.post("/runs/1/complete")


# --- the real loop, end to end ---------------------------------------------------------------------------------------------


def test_failed_verification_routes_back_to_research_repairs_and_a_report_from_current_versions(client, complete_env, world):
    r, _ids = world
    mesh = happy_mesh(r)
    srch = search()
    assert run_complete(client, complete_env, mesh, srch).status_code == 202

    # the actual graph loop, in order: coverage, repairs, targeted research, drafting from new evidence, re-verification
    assert mesh.steps() == ["coverage", "revise", "revise", "queries", "select", "queries", "select", "draft",
                            "verify", "verify", "verify", "verify", "coverage", "synth"]
    events = [e["event_type"] for e in client.get("/runs/1/events").json()]
    loop = [e for e in events if e in ("coverage_assessed", "coverage_routed", "correction_round_started", "claim_revised",
                                       "correction_round_finished", "claims_reverified", "report_created", "completion_finished")]
    assert loop == ["coverage_assessed", "coverage_routed", "correction_round_started", "claim_revised", "claim_revised",
                    "correction_round_finished", "claims_reverified", "coverage_assessed", "coverage_routed",
                    "report_created", "completion_finished"]
    done = client.get("/runs/1/completion").json()
    assert (done["complete_status"], done["corrective_rounds"], done["report_outcome"]) == (
        "completed", 1, "claims_all_requirements_addressed")
    assert done["stop_reason"] == "all evidence requirements were assessed as addressed" and done["coverage"]["fully_addressed"]

    # targeted tasks reached the Researcher with exactly what is missing, and the searches really ran
    queries_prompts = [c["messages"][1]["content"] for c in mesh.calls if "Write targeted web search queries" in c["messages"][0]["content"]]
    assert 'kind="date"' in queries_prompts[0] and GAP_REL in queries_prompts[0]
    assert 'kind="primary_documentation"' in queries_prompts[1] and GAP_PG in queries_prompts[1]
    assert srch.queries == [Q_REL, Q_PG]
    assert {s["requested_url"] for s in client.get("/runs/1/sources").json()} >= {A, B}

    # repairs are NEW immutable versions with fresh verdicts; the originals and their history are untouched
    byid = {c["id"]: c for c in client.get("/runs/1/claims").json()}
    for old, new, text in ((2, 6, B_NEW), (3, 7, C_NEW)):
        assert byid[new]["parent_claim_id"] == old and byid[new]["version"] == 2 and byid[new]["text"] == text
        assert byid[new]["provenance"]["revision"]["kind"] == "wording_repair" and byid[new]["provenance"]["revision"]["round"] == 1
        assert byid[new]["status"] == "supported" and byid[new]["eligible_for_report"] is True
        assert byid[old]["superseded_by"] == new and byid[old]["eligible_for_report"] is False
    assert byid[2]["status"] == "integrity_failed" and byid[2]["text"] == "SQLite keeps all of its data in a single file."
    assert byid[3]["status"] == "partially_supported" and byid[3]["semantic"]["verdict"] == "partially_supported"
    assert byid[4]["excluded"]["reason"].startswith("contradicted") and byid[4]["status"] == "contradicted"
    assert byid[1]["eligible_for_report"] and byid[5]["origin"] == "controlled_challenge" and not byid[5]["eligible_for_report"]

    # the report uses exactly the current verified versions, with the stored text and actual citations
    rep = client.get("/runs/1/report").json()
    texts = {it["claim_id"]: it["text"] for s in rep["sections"] for it in s["claims"]}
    assert texts == {1: Q0, 6: B_NEW, 8: N1, 7: C_NEW, 9: N2}
    assert [[it["claim_id"] for it in s["claims"]] for s in rep["sections"]] == [[1, 6, 8], [7, 9]]
    for section in rep["sections"]:
        for item in section["claims"]:
            stored = byid[item["claim_id"]]
            assert item["text"] == stored["text"] and item["claim_version"] == stored["version"]
            assert item["citations"] == [{**c, "quotes": c["quotes"]} for c in stored["citations"]]
            assert item["semantic_result_id"] == stored["semantic"]["id"]
    assert rep["ordering"] == "model" and rep["fully_addressed"] is True and rep["omissions"] == []
    assert SENTINEL not in rep["markdown"] and "Section 1" in rep["markdown"] and N1 in rep["markdown"]


def test_test_inputs_never_reach_a_prompt_the_coverage_input_or_the_report(client, complete_env, world):
    r, ids = world
    mesh = happy_mesh(r)
    run_complete(client, complete_env, mesh)
    assert all(SENTINEL not in m["content"] for c in mesh.calls for m in c["messages"])
    tasks = client.get("/runs/1/corrections").json()
    routed = {t["claim_id"] for rnd in tasks["rounds"] for t in rnd["tasks"] if t["claim_id"]}
    assert ids["T"] not in routed and ids["A"] not in routed
    cov = client.get("/runs/1/coverage?round=1").json()
    assert all(ids["T"] not in req["supporting_claim_ids"] for req in cov["requirements"])
    assert ids["T"] not in [it["claim_id"] for s in client.get("/runs/1/report").json()["sections"] for it in s["claims"]]


def test_coverage_report_and_tasks_are_inspectable_through_the_api(client, complete_env, world):
    r, _ = world
    run_complete(client, complete_env, happy_mesh(r))
    first = client.get("/runs/1/coverage?round=0").json()
    assert first["round"] == 0 and first["summary"]["unresolved"] == 2 and first["summary"]["fully_addressed"] is False
    by_key = {q["requirement_key"]: q for q in first["requirements"]}
    assert by_key["Q1.R2"]["gap"] == GAP_REL and by_key["Q1.R2"]["missing_kind"] == "date"
    assert by_key["Q1.R1"]["supporting_claim_ids"] == [1]
    assert client.get("/runs/1/coverage").json()["round"] == 1  # default: the latest assessment
    corr = client.get("/runs/1/corrections").json()
    (rnd,) = corr["rounds"]
    assert (rnd["planned_round"], rnd["corrective_round"]) == (0, 1)
    kinds = sorted((t["kind"], t["status"]) for t in rnd["tasks"])
    assert kinds == [("claim_exclusion", "done"), ("claim_repair", "done"), ("claim_repair", "done"),
                     ("missing_evidence", "done"), ("missing_evidence", "done")]
    assert rnd["coverage_before"]["unresolved"] == 2 and rnd["coverage_after"]["fully_addressed"] is True
    assert [[v["claim_id"] for v in lineage] for lineage in corr["revisions"]] == [[2, 6], [3, 7], [4]]  # D has one version, excluded
    assert corr["exclusions"][0]["claim_id"] == 4
    assert client.get("/runs/1/reports/1").json()["id"] == 1 and client.get("/runs/1/reports/99").status_code == 404


# --- two corrective rounds, then termination ------------------------------------------------------------------------------------


def test_two_corrective_rounds_then_the_loop_stops_because_rounds_are_exhausted(client, complete_env, web, settings):
    conn = db.connect(client.db_path)
    r, ids = seed_mixed(conn, settings)
    conn.close()
    web.html(A, f"<html><body><p>{RELEASE}</p></body></html>")
    web.html(B, f"<html><body><p>{MANUAL}</p></body></html>")
    mesh = FakeMesh(
        coverage=[
            coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "unresolved", [], GAP_REL, "date"),
                     ("Q2.R1", "addressed", [1], "", "none")),
            coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "partially_addressed", [6], "the release is not confirmed by a primary source", "primary_documentation"),
                     ("Q2.R1", "addressed", [1], "", "none")),
            coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "partially_addressed", [6, 7], "still no official release announcement", "primary_documentation"),
                     ("Q2.R1", "addressed", [1], "", "none"))],
        revise=[abandon_for_b(), abandon_for_c()],
        queries=[queries(Q_REL), queries(Q_PG)], select=[select(A), select(B)],
        draft=[draft(claim(1, N1, ("S2-P0", "Version 3.45 was released in January 2024"))),
               draft(claim(1, N1 + " (second source)", ("S3-P0", "readers do not block writers under multiversion concurrency control")))],
        verify=[verdict("supported", used=["S2-P0"]), verdict("supported", used=["S3-P0"])],
        synth=synth((1, [1, 6, 7])))
    mesh.replies["revise"] = [abandon_for_b(), abandon_for_c()]
    complete_env(mesh, search())
    client.post("/runs/1/complete")
    done = client.get("/runs/1/completion").json()
    assert done["corrective_rounds"] == 2 and done["complete_status"] == "completed"
    assert done["stop_reason"] == "corrective rounds exhausted (2)"
    assert mesh.steps().count("coverage") == 3  # initial + after each of the two corrective rounds
    rounds = client.get("/runs/1/corrections").json()["rounds"]
    assert [rnd["corrective_round"] for rnd in rounds if rnd["executed"]] == [1, 2]
    last = rounds[-1]  # planned by the final assessment, never run: recorded as skipped with the stop reason
    assert not last["executed"] and all(t["status"] == "skipped" for t in last["tasks"])
    assert all("corrective rounds exhausted (2)" in t["outcome"]["reason"] for t in last["tasks"])
    reasons = {e["claim_id"]: e["reason"] for e in client.get("/runs/1/corrections").json()["exclusions"]}
    assert reasons[2].startswith("abandoned by the Reviser") and reasons[3].startswith("abandoned by the Reviser")
    rep = client.get("/runs/1/report").json()
    assert rep["outcome"] == "claims_with_gaps" and rep["fully_addressed"] is False and rep["corrective_rounds"] == 2
    assert [u["requirement_key"] for u in rep["unresolved_requirements"]] == ["Q1.R2"]
    assert r["sq1"] == 1 and ids["B"] == 2


def abandon_for_b():
    from tests.fakes import abandon

    return abandon("the stored passages do not permit an accurate version of this claim")


def abandon_for_c():
    from tests.fakes import abandon

    return abandon("the claim bundles an unsupported maintenance statement")


# --- stopping: no progress, no work, zero rounds, all addressed ---------------------------------------------------------------------


def test_no_progress_is_detected_and_the_unchanged_search_is_not_repeated(client, complete_env, web, settings):
    conn = db.connect(client.db_path)
    r, _ = seed_mixed(conn, settings)
    conn.close()
    stuck = coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "unresolved", [], GAP_REL, "date"),
                     ("Q2.R1", "addressed", [1], "", "none"))
    from tests.fakes import abandon

    mesh = FakeMesh(coverage=[stuck, stuck], revise=[abandon(), abandon()], queries=queries(Q_REL), select=select(),
                    synth=synth((1, [1])))
    srch = FakeSearch({Q_REL: [(A, "Release notes", "snippet")]})
    web.html(A, "<html><body><p>Unrelated text about gardening and tomatoes.</p></body></html>")
    complete_env(mesh, srch)
    client.post("/runs/1/complete")
    done = client.get("/runs/1/completion").json()
    assert done["stop_reason"].startswith("no progress") and done["corrective_rounds"] == 1
    assert mesh.steps().count("coverage") == 2 and srch.queries == [Q_REL]  # one search, never repeated
    assert r["run"] == 1


def test_a_gap_that_was_already_searched_creates_a_skipped_task_with_the_reason(conn, settings):
    from backend import correction

    r, _ = seed_mixed(conn, settings)
    rows = [{"requirement_key": "Q1.R2", "subquestion_id": r["sq1"], "requirement_text": "release facts", "status": "unresolved",
             "gap": GAP_REL, "missing_kind": "date"}]
    first = correction.propose_tasks(conn, r["run"], rows, settings, searches_left=5)
    stored = correction.persist_tasks(conn, r["run"], 0, first)
    task = next(t for t in stored if t["kind"] == "missing_evidence")["task_id"]
    unrun = correction.propose_tasks(conn, r["run"], rows, settings, searches_left=5)
    assert "skip" not in next(p for p in unrun if p["kind"] == "missing_evidence")  # planned but never run: not 'searched'
    with db.tx(conn):
        correction.finish_task(conn, task, "done", {"new_source_ids": []})  # now it has really run
    again = correction.propose_tasks(conn, r["run"], rows, settings, searches_left=5)
    repeat = next(p for p in again if p["kind"] == "missing_evidence")
    assert "already searched" in repeat["skip"]
    changed = [{**rows[0], "gap": "still no official announcement"}]
    assert "skip" not in next(p for p in correction.propose_tasks(conn, r["run"], changed, settings, 5) if p["kind"] == "missing_evidence")


def test_zero_corrective_rounds_goes_straight_to_the_report_with_gaps_listed(client, complete_env, world):
    mesh = FakeMesh(coverage=coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "unresolved", [], GAP_REL, "date"),
                                      ("Q2.R1", "unresolved", [], GAP_PG, "detail")), synth=synth((1, [1])))
    run_complete(client, complete_env, mesh, max_correction_rounds=0)
    done = client.get("/runs/1/completion").json()
    assert done["stop_reason"] == "corrective rounds exhausted (0)" and done["corrective_rounds"] == 0
    assert mesh.steps() == ["coverage", "synth"]
    rep = client.get("/runs/1/report").json()
    assert rep["outcome"] == "claims_with_gaps" and len(rep["unresolved_requirements"]) == 2
    tasks = client.get("/runs/1/corrections").json()["rounds"][0]["tasks"]
    assert {t["status"] for t in tasks if t["kind"] in ("claim_repair", "missing_evidence")} == {"skipped"}
    assert all("corrective rounds exhausted" in t["outcome"]["reason"] for t in tasks if t["status"] == "skipped")


# --- one cumulative budget ---------------------------------------------------------------------------------------------------------


def test_the_budget_is_cumulative_across_nodes_and_rounds_and_reserves_room_for_the_report(client, complete_env, world):
    r, _ = world
    mesh = happy_mesh(r, synth=synth((1, [1, 6]), (2, [7])), coverage=[
        happy_mesh(r).replies["coverage"][0],
        coverage(("Q1.R1", "addressed", [1, 6], "", "none"), ("Q1.R2", "unresolved", [], GAP_REL, "date"),
                 ("Q2.R1", "addressed", [7], "", "none"))],
        verify=[verdict("supported", used=[r["p"](0)]), verdict("supported", used=[r["p"](2)])])
    run_complete(client, complete_env, mesh, max_model_requests=9)
    assert len(mesh.calls) == 9  # coverage, 2 revisions, 1 search task (2), 2 re-verifications, coverage, synthesis
    done = client.get("/runs/1/completion").json()
    assert done["budget_used_of_limit"]["model_requests"] == [9, 9] and done["budget_used_of_limit"]["search_queries"] == [1, 8]
    tasks = {(t["kind"], t["requirement_key"]): t for t in client.get("/runs/1/corrections").json()["rounds"][0]["tasks"]}
    assert tasks[("missing_evidence", "Q1.R2")]["status"] == "done"
    skipped = tasks[("missing_evidence", "Q2.R1")]
    assert skipped["status"] == "skipped" and "reserving" in skipped["outcome"]["reason"]
    events = {e["event_type"]: e["payload"] for e in client.get("/runs/1/events").json()}
    assert "reserving" in events["drafting_skipped"]["reason"]
    assert client.get("/runs/1/report").json()["ordering"] == "model"  # the reserve kept the synthesis possible


@pytest.mark.parametrize("cap,where", [(0, "coverage"), (1, "after the first assessment"), (2, "after the first assessment")])
def test_budget_exhaustion_stops_the_run_and_still_produces_an_honest_report(client, complete_env, world, cap, where):
    r, _ = world
    mesh = happy_mesh(r)
    run_complete(client, complete_env, mesh, max_model_requests=cap)
    done = client.get("/runs/1/completion").json()
    assert done["complete_status"] == "stopped" and done["stop_reason"]
    assert len(mesh.calls) <= cap  # failed or refused requests are never over-spent
    rep = client.get("/runs/1/report").json()
    assert rep["ordering"] == "deterministic" and "synthesizer not called" in (rep["ordering_reason"] or "")
    assert rep["stop_reason"] == done["stop_reason"]
    assert [it["claim_id"] for s in rep["sections"] for it in s["claims"]] == [1]  # only the current eligible claim
    if cap == 0:
        assert rep["coverage_round"] is None and {q["status"] for q in rep["requirements"]} == {"not_assessed"}


def test_retries_and_failed_requests_count_against_the_same_budget(client, complete_env, world):
    r, _ = world
    mesh = happy_mesh(r, revise=["not json", revise(B_NEW, (r["p"](0), SINGLE_FILE)), RuntimeError("down")])
    run_complete(client, complete_env, mesh, max_model_requests=12)
    used = client.get("/runs/1/completion").json()["budget_used_of_limit"]["model_requests"]
    calls = client.get("/runs/1/model-calls").json()
    assert used[0] == len([c for c in calls if c["agent"] in ("coverage", "reviser", "researcher", "verifier", "drafter", "synthesizer")]) <= 12
    assert any(c["agent"] == "reviser" and not c["ok"] for c in calls)  # the invalid reply and the failed call were charged
    tasks = client.get("/runs/1/corrections").json()["rounds"][0]["tasks"]
    assert any(t["kind"] == "claim_repair" and t["status"] == "failed" for t in tasks)  # a failed task does not fake success
    assert client.get("/runs/1/completion").json()["complete_status"] in ("completed", "stopped")


# --- Phase 4 audit: corrective evidence must be usable, within a hard, separate budget -----------------------------------------


def events_of(client):
    return {e["event_type"]: e["payload"] for e in client.get("/runs/1/events").json()}


def test_corrective_evidence_becomes_bounded_candidates_that_are_verified_and_can_enter_the_report(client, complete_env, world):
    r, _ = world
    run_complete(client, complete_env, happy_mesh(r), max_candidate_claims=2)
    ev = events_of(client)
    assert ev["candidates_drafted"]["claim_ids"] == [8, 9]
    assert ev["candidates_drafted"]["candidate_claims_used_of_limit"] == [2, 2] and ev["candidates_drafted"]["from_source_ids"] == [2, 3]
    done = client.get("/runs/1/completion").json()
    assert done["budget_used_of_limit"]["candidate_claims"] == [2, 2]
    byid = {c["id"]: c for c in client.get("/runs/1/claims").json()}
    for cid, text in ((8, N1), (9, N2)):
        c = byid[cid]
        assert c["origin"] == "drafted" and c["parent_claim_id"] is None and c["text"] == text
        assert c["provenance"] == {"round": 1, "origin": "targeted_research", "candidate": True}
        assert (c["integrity_verdict"], c["semantic_verdict"], c["eligible_for_report"]) == ("passed", "supported", True)
    assert all(byid[i]["eligible_for_report"] for i in (1, 6, 7))  # accepted claims were not displaced or weakened
    rep = client.get("/runs/1/report").json()
    got = {it["claim_id"]: it for s in rep["sections"] for it in s["claims"]}
    assert set(got) == {1, 6, 7, 8, 9} and got[8]["text"] == N1 and got[8]["citations"] == byid[8]["citations"]


def test_the_candidate_cap_is_hard_and_trims_the_drafters_output(client, complete_env, world):
    r, _ = world
    mesh = happy_mesh(r, synth=synth((1, [1, 6, 8]), (2, [7])))
    run_complete(client, complete_env, mesh, max_candidate_claims=1)
    assert events_of(client)["draft_truncated"] == {"returned": 2, "kept": 1, "max_claims": 1}
    assert client.get("/runs/1/completion").json()["budget_used_of_limit"]["candidate_claims"] == [1, 1]
    assert mesh.steps().count("verify") == 3  # two repairs and the single candidate: the second one was never created
    assert len([c for c in client.get("/runs/1/claims").json() if c["provenance"].get("candidate")]) == 1


def test_with_no_candidate_budget_research_still_runs_and_the_skipped_drafting_is_recorded(client, complete_env, world):
    r, _ = world
    p = r["p"]
    mesh = happy_mesh(
        r, verify=[verdict("supported", used=[p(0)]), verdict("supported", used=[p(2)])],
        coverage=[happy_mesh(r).replies["coverage"][0],
                  coverage(("Q1.R1", "addressed", [1, 6], "", "none"), ("Q1.R2", "unresolved", [], GAP_REL, "date"),
                           ("Q2.R1", "addressed", [7], "", "none"))],
        synth=synth((1, [1, 6]), (2, [7])))
    run_complete(client, complete_env, mesh, max_candidate_claims=0, max_correction_rounds=1)
    assert "draft" not in mesh.steps()
    skipped = events_of(client)["drafting_skipped"]
    assert skipped["reason"] == "candidate-claim budget exhausted" and skipped["new_source_ids"] == [2, 3]
    assert skipped["candidate_claims_used_of_limit"] == [0, 0]
    rep = client.get("/runs/1/report").json()
    assert [it["claim_id"] for s in rep["sections"] for it in s["claims"]] == [1, 6, 7]  # accepted claims untouched
    assert {q["requirement_key"]: q["status"] for q in rep["requirements"]}["Q1.R2"] == "unresolved"


def test_an_unsupported_candidate_is_kept_in_history_excluded_and_the_gap_stays_reported(client, complete_env, world):
    r, _ = world
    p = r["p"]
    mesh = happy_mesh(
        r, verify=[verdict("supported", used=[p(0)]), verdict("supported", used=[p(2)]),
                   verdict("unsupported", used=[], unsupported=["the release date"]), verdict("supported", used=["S3-P0"])],
        coverage=[happy_mesh(r).replies["coverage"][0],
                  coverage(("Q1.R1", "addressed", [1, 6], "", "none"), ("Q1.R2", "unresolved", [], "still no date", "date"),
                           ("Q2.R1", "addressed", [7, 9], "", "none"))],
        synth=synth((1, [1, 6]), (2, [7, 9])))
    run_complete(client, complete_env, mesh, max_correction_rounds=1)
    byid = {c["id"]: c for c in client.get("/runs/1/claims").json()}
    assert byid[8]["status"] == "unsupported" and byid[8]["eligible_for_report"] is False and byid[8]["text"] == N1  # not deleted
    assert byid[8]["excluded"]["reason"].startswith("unsupported")  # routed to exclusion by its stored verdict
    assert byid[9]["eligible_for_report"] is True  # the other candidate is judged on its own
    rep = client.get("/runs/1/report").json()
    assert 8 not in [it["claim_id"] for s in rep["sections"] for it in s["claims"]] and 9 in [it["claim_id"] for s in rep["sections"] for it in s["claims"]]
    assert [u["requirement_key"] for u in rep["unresolved_requirements"]] == ["Q1.R2"]  # the remaining gap is reported
    assert client.get("/runs/1/corrections").json()["exclusions"][-1]["claim_id"] == 8


def test_the_report_has_its_own_size_limit_separate_from_the_candidate_budget(client, complete_env, world):
    r, _ = world
    run_complete(client, complete_env, happy_mesh(r), max_report_claims=2)
    rep = client.get("/runs/1/report").json()
    assert [it["claim_id"] for s in rep["sections"] for it in s["claims"]] == [1, 6]
    assert {o["claim_id"]: o["reason"] for o in rep["omissions"]} == {c: "report claim limit (2) reached" for c in (7, 8, 9)}
    assert len(client.get("/runs/1/claims?eligible_only=true").json()) == 5  # nothing was deleted or made ineligible
    assert client.get("/runs/1/completion").json()["budget_used_of_limit"]["candidate_claims"] == [2, 4]


@pytest.mark.parametrize("cap", range(17))
def test_the_shared_budget_is_never_exceeded_and_a_report_always_follows(client, complete_env, world, cap):
    """Reserved finalization requests are held back from correction, never added on top of the cap."""
    r, _ = world
    mesh = happy_mesh(r)
    run_complete(client, complete_env, mesh, max_model_requests=cap)
    done = client.get("/runs/1/completion").json()
    assert len(mesh.calls) <= cap and done["budget_used_of_limit"]["model_requests"] == [len(mesh.calls), cap]
    assert done["complete_status"] in ("completed", "stopped") and client.get("/runs/1/report").status_code == 200


def test_failed_and_retried_requests_never_push_the_job_over_the_cap(client, complete_env, world):
    r, _ = world
    mesh = happy_mesh(r, verify=["not json"])  # every verification fails validation twice
    run_complete(client, complete_env, mesh, max_model_requests=12)
    assert len(mesh.calls) <= 12
    calls = client.get("/runs/1/model-calls").json()
    assert any(c["agent"] == "verifier" and not c["ok"] for c in calls)
    assert client.get("/runs/1/completion").json()["complete_status"] in ("completed", "stopped")
    assert client.get("/runs/1/report").status_code == 200


# --- Phase 4 audit: a compound claim becomes separate atomic children ------------------------------------------------------------

PART_A = ("Postgres can handle hundreds of concurrent clients.", [("S1-P2", "It can handle hundreds of concurrent clients")])
PART_B = ("Postgres results may vary.", [("S1-P2", "though results may vary")])


def test_a_compound_claim_is_split_into_atomic_children_each_verified_afresh(client, complete_env, world):
    r, _ = world
    p = r["p"]
    mesh = happy_mesh(
        r, revise=[revise(B_NEW, (p(0), SINGLE_FILE)), split(PART_A, PART_B)],
        verify=[verdict("supported", used=[p(0)]), verdict("supported", used=[p(2)]), verdict("unsupported", used=[]),
                verdict("supported", used=["S2-P0"]), verdict("supported", used=["S3-P0"])],
        coverage=[happy_mesh(r).replies["coverage"][0],
                  coverage(("Q1.R1", "addressed", [1, 6], "", "none"), ("Q1.R2", "addressed", [9], "", "none"),
                           ("Q2.R1", "addressed", [7, 10], "", "none"))],
        synth=synth((1, [1, 6, 9]), (2, [7, 10])))
    run_complete(client, complete_env, mesh)
    byid = {c["id"]: c for c in client.get("/runs/1/claims").json()}
    for cid, part in ((7, 1), (8, 2)):
        child = byid[cid]
        assert (child["parent_claim_id"], child["version"]) == (3, 2) and child["origin"] == "drafted"
        assert child["provenance"]["revision"]["kind"] == "split_into_atomic"
        assert (child["provenance"]["revision"]["part"], child["provenance"]["revision"]["of"]) == (part, 2)
    assert byid[3]["superseded_by"] == 8 and byid[3]["status"] == "partially_supported" and byid[3]["eligible_for_report"] is False
    assert (byid[7]["semantic_verdict"], byid[7]["eligible_for_report"]) == ("supported", True)  # each child, its own verdict
    assert (byid[8]["semantic_verdict"], byid[8]["eligible_for_report"]) == ("unsupported", False)
    assert byid[8]["excluded"] is not None and byid[8]["text"] == PART_B[0]  # kept in history, out of the report
    rep = client.get("/runs/1/report").json()
    assert sorted(it["claim_id"] for s in rep["sections"] for it in s["claims"]) == [1, 6, 7, 9, 10]
    family = next(f for f in client.get("/runs/1/corrections").json()["revisions"] if f[0]["claim_id"] == 3)
    assert [v["claim_id"] for v in family] == [3, 7, 8]  # lineage shows the parent and BOTH children
    assert mesh.steps().count("verify") == 5


def test_a_split_that_the_remaining_budget_cannot_verify_is_not_made(conn, settings):
    from backend.agents import reviser
    from tests.fakes import split

    r, ids = seed_mixed(conn, settings)
    mesh = FakeMesh(revise=split(PART_A, PART_B))
    out = reviser.revise_claim(conn, LLM(mesh, settings), Budget(settings), settings, r["run"], ids["C"], planned_round=0,
                               task_id=1, failure={}, max_parts=1)
    assert out["action"] == "skipped" and "exceed the remaining verification budget" in out["reason"]
    assert claims.get_claim(conn, r["run"], ids["C"])["superseded_by"] is None
    assert len(claims.list_claims(conn, r["run"])) == 5


def test_every_requirement_stays_visible_in_the_report_and_old_reports_never_change(client, complete_env, world):
    r, _ = world
    run_complete(client, complete_env, happy_mesh(r), max_correction_rounds=0)
    rep = client.get("/runs/1/report").json()
    assert [q["requirement_key"] for q in rep["requirements"]] == ["Q1.R1", "Q1.R2", "Q2.R1"]  # unresolved ones included
    assert {q["requirement_key"] for q in rep["unresolved_requirements"]} == {"Q1.R2", "Q2.R1"} and len(rep["sections"]) == 2
    import json as _json

    frozen = _json.dumps(rep, sort_keys=True)
    conn = db.connect(client.db_path)
    with db.tx(conn):
        newer = claims.add_claim(conn, 1, 1, "A newer version of claim one.", [("S1-P0", Q0)], origin="drafted", parent_claim_id=1, version=2)
        claims.exclude_claim(conn, 1, 6, "retired after the report", 3)
    conn.close()
    assert newer > 5 and _json.dumps(client.get("/runs/1/reports/1").json(), sort_keys=True) == frozen
    assert client.get("/runs/1/claims/1").json()["superseded_by"] == newer  # the claim moved on; the report did not


# --- the shared budget across the whole end-to-end job ---------------------------------------------------------------------------------


def test_end_to_end_option_uses_one_budget_for_collection_verification_correction_and_report(client, complete_env, web):
    page = "SQLite is a C library embedded directly in the application process. The database is a single file on disk."
    web.html(A, f"<html><head><title>Guide</title></head><body><p>{page}</p></body></html>")
    from tests.fakes import obligations, plan

    mesh = FakeMesh(
        planner=plan(("How is SQLite deployed and used?", ["deployment model"])), queries=queries("sqlite deployment model"),
        obligations=obligations(Q1_R1=[("SQLite", "deployment model")]),
        select=select(A),
        draft=draft(claim(1, "SQLite is a C library embedded directly in the application process.",
                          ("S1-P0", "SQLite is a C library embedded directly in the application process."))),
        verify=verdict("supported", used=["S1-P0"]),
        coverage=coverage(("Q1.R1", "addressed", [1], "", "none")), synth=synth((1, [1])))
    complete_env(mesh, FakeSearch({"sqlite deployment model": [(A, "Guide", "s")]}))
    run_id = client.post("/runs", json={"question": "How is SQLite deployed?"}).json()["id"]
    assert client.post(f"/runs/{run_id}/research?complete=true").status_code == 202
    assert mesh.steps() == ["planner", "queries", "select", "draft", "verify", "obligations", "coverage", "synth"]
    assert client.get(f"/runs/{run_id}/research").json()["status"] == "collection_finished"
    assert client.get(f"/runs/{run_id}/verification").json()["verify_status"] == "completed"
    done = client.get(f"/runs/{run_id}/completion").json()
    assert done["complete_status"] == "completed" and done["report_outcome"] == "claims_all_requirements_addressed"
    assert done["budget_used_of_limit"]["model_requests"] == [8, 20]  # ONE counter for all eight requests
    assert client.post(f"/runs/{run_id}/research?complete=true").status_code == 409


_ = (PARAS, claims)
