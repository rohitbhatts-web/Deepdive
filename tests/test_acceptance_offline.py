"""OFFLINE ACCEPTANCE FIXTURE for the acceptance question, through the real API, production graph, database and report builder.

Every model reply and every web page below is a scripted FIXTURE written for this test (the page texts are synthetic,
written in our own words, and titled "FIXTURE page N"). These tests show that the pipeline CAN carry a complete answer to both halves of the question through planning, collection, drafting,
verification, coverage, consistency and synthesis within the configured limits, and that the stored report says what it should. They do NOT measure model
accuracy, research quality or how a live run will behave.
"""

from backend import graph
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
from tests.test_budget_policy import assert_honest, state

QUESTION = "How do Git merge and Git rebase differ in their effect on commit history, and when should a developer choose each?"
SUBQ = {
    1: ("How does Git merge affect commit history?", ["effect of merge on commit history"]),
    2: ("How does Git rebase affect commit history?", ["effect of rebase on commit history"]),
    3: ("When should a developer choose Git merge?", ["situations that favour merge"]),
    4: ("When should a developer choose Git rebase?", ["situations that favour rebase"]),
}
URL = {n: f"https://example.test/fixture-git-{n}" for n in SUBQ}
# FIXTURE page texts: one paragraph each, so each page is one passage S{n}-P0.
PAGE = {
    1: "A merge joins two branches by adding one merge commit that has two parents. Existing commits are left unchanged, so the history records how the "
       "branches really developed.",
    2: "A rebase replays the commits of a branch onto a different starting commit. It creates new commits with new identifiers, so the history becomes linear and the "
       "original commits are rewritten.",
    3: "Merging is the safer choice for shared or public branches, because it never rewrites commits that other developers already have.",
    4: "Rebasing suits local feature branches that nobody else uses yet, to keep a clean linear history; commits that others have already pulled should not "
       "be rebased.",
}
CLAIMS = [  # the drafter returns SIX claims grouped by subquestion; the drafting cap keeps one per subquestion first
    (1, "A merge adds one merge commit that has two parents.", "A merge joins two branches by adding one merge commit that has two parents."),
    (1, "A merge leaves existing commits unchanged.", "Existing commits are left unchanged"),
    (2, "A rebase creates new commits with new identifiers, so the original commits are rewritten.",
     "It creates new commits with new identifiers, so the history becomes linear and the original commits are rewritten."),
    (3, "Merging is the safer choice for shared or public branches because it never rewrites commits others already have.",
     "Merging is the safer choice for shared or public branches, because it never rewrites commits that other developers already have."),
    (4, "Rebasing suits local feature branches that nobody else uses yet, to keep a linear history.",
     "Rebasing suits local feature branches that nobody else uses yet, to keep a clean linear history"),
    (4, "Commits that others have already pulled should not be rebased.", "commits that others have already pulled should not be rebased"),
]


def pages(web):
    for n, text in PAGE.items():
        web.html(URL[n], f"<html><head><title>FIXTURE page {n}</title></head><body><p>{text}</p></body></html>")


def acceptance_mesh(verify_replies, **extra):
    replies = {
        "planner": plan(*SUBQ.values()),
        "queries": [queries(f"git fixture query {n}") for n in SUBQ], "select": [select(URL[n]) for n in SUBQ],
        "draft": draft(*[claim(sq, text, (f"S{sq}-P0", quote)) for sq, text, quote in CLAIMS]),
        "verify": verify_replies, "obligations": obligations(),
        "coverage": coverage(("Q1.R1", "addressed", [1, 5], "", "none"), ("Q2.R1", "addressed", [2], "", "none"),
                             ("Q3.R1", "addressed", [3], "", "none"), ("Q4.R1", "addressed", [4], "", "none")),
        "consistency": {"claims_reviewed": [1, 2, 3, 4, 5], "findings": []},
        "synth": synth((1, [1, 5]), (2, [2]), (3, [3]), (4, [4])),
    }
    replies.update(extra)
    return FakeMesh(**replies)


def ok(n):
    return verdict("supported", used=[f"S{n}-P0"])


# Claims are kept and stored one per subquestion first: 1 (sq1), 2 (sq2), 3 (sq3), 4 (sq4), 5 (sq1, second merge claim).
KEPT_ORDER = [1, 2, 3, 4, 1]


def run(client, complete_env, web, mesh, **overrides):
    pages(web)
    complete_env(mesh, FakeSearch({f"git fixture query {n}": [(URL[n], f"FIXTURE page {n}", "s")] for n in SUBQ}), **overrides)
    run_id = client.post("/runs", json={"question": QUESTION}).json()["id"]
    assert client.post(f"/runs/{run_id}/research?complete=true").status_code == 202
    return run_id


def test_offline_fixture_complete_answer_to_both_halves_within_the_limits(client, complete_env, web):
    mesh = acceptance_mesh([ok(n) for n in KEPT_ORDER])
    run_id = run(client, complete_env, web, mesh)
    st = state(client, run_id)
    steps = mesh.steps()
    # every stage ran, in order, within the unchanged 20-request limit (one request left over)
    assert steps == ["planner"] + ["queries", "select"] * 4 + ["relevance", "draft"] + ["verify"] * 5 + ["obligations", "coverage", "consistency", "synth"]
    used, limit = st["completion"]["budget_used_of_limit"]["model_requests"]
    assert (used, limit) == (len(steps), 20) and used <= limit
    assert st["curtailed"] == [] and st["verification"]["verify_status"] == "completed"
    # the drafting cap kept 5 of the 6 returned claims, one per subquestion first: both halves of the question are represented
    drafted = next(e for e in st["events"] if e["event_type"] == "claims_drafted")["payload"]["claim_ids"]
    by_id = {c["id"]: c for c in st["claims"]}
    assert [by_id[i]["subquestion_id"] for i in drafted] == KEPT_ORDER
    # completed assessments
    done = st["completion"]
    assert done["complete_status"] == "completed" and done["report_outcome"] == "claims_all_requirements_addressed"
    rep = st["report"]
    assert rep["coverage_assessed"] is True and rep["unresolved_requirements"] == [] and rep["unassessed_requirements"] == []
    assert rep["consistency"]["status"] == "reviewed" and rep["consistency"]["has_conflicts"] is False and rep["ordering"] == "model"
    # both halves answered: every subquestion's section has a direct answer, and the answers state the effect and the choice
    direct = {s["subquestion_id"]: [i["text"] for i in s["claims"]] for s in rep["sections"]}
    assert all(direct[sq] for sq in SUBQ)
    effects = " ".join(direct[1] + direct[2]).lower()
    choices = " ".join(direct[3] + direct[4]).lower()
    assert "merge commit" in effects and "rewritten" in effects
    assert "shared" in choices and "local feature branches" in choices
    # relevant citations: each reported claim cites the page of its own subquestion, and its quote is in that passage
    for s in rep["sections"]:
        for i in s["claims"]:
            sq = by_id[i["claim_id"]]["subquestion_id"]
            assert i["citations"] and all(c["url"] == URL[sq] and all(q in PAGE[sq] for q in c["quotes"]) for c in i["citations"])
    assert_honest(st)
    assert "OFFLINE" not in rep["markdown"]  # the report itself is the production rendering; the FIXTURE label lives in the page titles
    assert all(f"FIXTURE page {n}" in rep["markdown"] for n in SUBQ)


def test_offline_fixture_partial_consistency_with_a_known_conflict_is_shown_with_its_limitation(client, complete_env, web):
    conflict = {"claims_reviewed": [1, 2], "findings": [{"claim_ids": [1, 2], "kind": "conflict", "evidence": [],
                                                         "explanation": "FIXTURE: scripted conflict between the merge and rebase claims."}]}
    mesh = acceptance_mesh([ok(n) for n in KEPT_ORDER], consistency=conflict)
    st = state(client, run(client, complete_env, web, mesh))
    c = st["report"]["consistency"]
    assert c["status"] == "partial" and c["has_conflicts"] is True and sorted(c["unchecked_claim_ids"]) == [3, 4, 5]
    labelled = {i["claim_id"] for s in st["report"]["sections"] for i in s["claims"] + s["context"] if i.get("conflicts")}
    assert labelled == {1, 2}  # both conflicting claims carry the warning; nothing is declared false
    md = st["report"]["markdown"]
    assert "POSSIBLE CONFLICT" in md and "Status: partial" in md and "Not compared" in md
    assert_honest(st)


def test_offline_fixture_the_supported_retry_path_applies_the_same_policy(client, complete_env, web):
    """A collected run with no claims, completed through execute_completion(verify_first=True), the path POST /runs/{id}/retry uses."""
    collect = acceptance_mesh([ok(n) for n in KEPT_ORDER])
    pages(web)
    svc = complete_env(collect, FakeSearch({f"git fixture query {n}": [(URL[n], f"FIXTURE page {n}", "s")] for n in SUBQ}))
    run_id = client.post("/runs", json={"question": QUESTION}).json()["id"]
    assert client.post(f"/runs/{run_id}/research").status_code == 202  # collection only: no drafting, no assessment
    assert "draft" not in collect.steps()
    retry = acceptance_mesh([ok(n) for n in KEPT_ORDER])
    svc = complete_env(retry, FakeSearch({}), max_search_queries=0, max_page_fetches=0, max_model_requests=9)
    from backend import db
    conn = db.connect(client.db_path)
    with db.tx(conn):
        db.set_complete_status(conn, run_id, "completing", None)
    conn.close()
    graph.execute_completion(run_id, svc, verify_first=True)
    st = state(client, run_id)
    held = [h for h in st["held"] if h["phase"] in ("drafting", "verification")]
    assert held and all(h["held"] == 3 and h["held_seconds"] == 45.0 for h in held)  # the final assessment is held in the retry path too
    steps = retry.steps()
    # 9 requests: drafting 1 leaves 5 optional (3 held): cap 4 claims (1 of slack), 4 verifications, then the held final assessment, then synthesis with the last
    assert steps == ["draft"] + ["verify"] * 4 + ["obligations", "coverage", "consistency", "synth"]
    assert st["report"]["coverage_assessed"] is True
    assert_honest(st)


def test_offline_fixture_a_coverage_retry_cannot_take_the_request_held_for_the_consistency_check(client, complete_env, web):
    """Exactly coverage + consistency are left when coverage starts (one verifier retry used the spare request), and coverage's first reply is invalid.
    Its corrective retry would need the consistency check's request; it must not take it."""
    verify = [ok(1), "invalid verifier reply", ok(2), ok(3), ok(4), ok(1)]  # one validation retry: verification uses 6 of the 6 optional requests
    valid_coverage = acceptance_mesh([]).replies["coverage"][0]
    mesh = acceptance_mesh(verify, coverage=["not json: an invalid coverage reply", valid_coverage])
    st = state(client, run(client, complete_env, web, mesh))
    steps = mesh.steps()
    assert len(steps) <= 20
    assert steps.count("coverage") == 1  # no corrective retry: it would have used the consistency check's request
    assert steps[-1] == "consistency"  # the consistency check got its own held request
    rep = st["report"]
    assert rep["consistency"]["status"] in ("reviewed", "partial")
    assert rep["coverage_assessed"] is False and len(rep["unassessed_requirements"]) == 4  # coverage did not finish: honestly "not assessed"
    assert st["completion"]["complete_status"] in ("stopped", "failed") and "consistency" in st["completion"]["stop_reason"]
    assert_honest(st)


def test_offline_fixture_a_coverage_failure_still_reports_runs_the_consistency_check_and_marks_the_job_failed(client, complete_env, web):
    """Budget allows the retry, but both coverage replies are invalid: no assessment exists, and that is reported, not hidden behind a missing report."""
    mesh = acceptance_mesh([ok(n) for n in KEPT_ORDER], coverage=["not json", "still not json"])
    st = state(client, run(client, complete_env, web, mesh))
    assert mesh.steps().count("coverage") == 2 and "consistency" in mesh.steps()  # one corrective retry (budget allowed it), then the check
    rep = st["report"]
    assert rep["coverage_assessed"] is False and len(rep["unassessed_requirements"]) == 4
    assert rep["consistency"]["status"] in ("reviewed", "partial")
    assert st["completion"]["complete_status"] == "failed" and "coverage assessment failed" in st["completion"]["stop_reason"]
    assert "coverage assessment failed" in rep["stop_reason"]
    assert_honest(st)
