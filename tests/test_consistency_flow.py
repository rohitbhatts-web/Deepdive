"""The cross-claim consistency check inside the REAL LangGraph completion loop (coverage -> corrections -> final coverage -> consistency check -> synthesis -> report).

Model replies are SCRIPTED fixtures. These tests prove ordering, budgets and what reaches the stored report and the API; they do not show that a real model detects
real contradictions. Claim IDs below (1, 6, 7, 8, 9) are the eligible claims of the scripted run in test_phase4_loop.
"""

import pytest

from tests.fakes import synth
from tests.test_phase4_loop import happy_mesh, run_complete, world  # noqa: F401  (the fixture)

ELIGIBLE = [1, 6, 7, 8, 9]
CONFLICT = {"claims_reviewed": ELIGIBLE, "findings": [{"claim_ids": [1, 6], "kind": "conflict", "explanation": "scripted: these two cannot both hold as worded", "evidence": []}]}


def items(rep):
    return [i for s in rep["sections"] for i in s["claims"] + s["context"]]


def flagged(rep):
    return {i["claim_id"]: [c["with_claim_ids"] for c in i["conflicts"]] for i in items(rep) if i.get("conflicts")}


def run(client, complete_env, world, **overrides):  # noqa: F811
    r, _ = world
    mesh = happy_mesh(r, consistency=CONFLICT, **{k: overrides.pop(k) for k in list(overrides) if k in ("synth",)})
    run_complete(client, complete_env, mesh, **overrides)
    return mesh, client.get("/runs/1/report").json(), client.get("/runs/1/completion").json()


def test_the_check_runs_once_between_the_final_coverage_and_the_synthesis_and_its_warning_reaches_the_api(client, complete_env, world):  # noqa: F811
    mesh, rep, done = run(client, complete_env, world)
    assert mesh.steps()[-3:] == ["coverage", "consistency", "synth"] and mesh.steps().count("consistency") == 1
    assert done["complete_status"] == "completed" and rep["ordering"] == "model"
    c = rep["consistency"]
    assert c["status"] == "reviewed" and c["has_conflicts"] is True and c["shown_claim_ids"] == ELIGIBLE
    assert flagged(rep) == {1: [[6]], 6: [[1]]}
    assert sorted(i["claim_id"] for i in items(rep)) == sorted(ELIGIBLE)  # every eligible claim is in the report, warned or not
    sent = next(call for call in mesh.calls if "Synthesizer" in call["messages"][0]["content"])
    assert "<possible_conflict" in sent["messages"][1]["content"]
    # the check is a request like any other: counted on the same job budget
    assert done["budget_used_of_limit"]["model_requests"][0] == len(mesh.calls)


def test_a_synthesizer_cannot_drop_a_possibly_conflicting_claim_as_a_duplicate(client, complete_env, world):  # noqa: F811
    _, rep, _ = run(client, complete_env, world, synth=synth((1, [1, 8]), (2, [7, 9]), omitted=[(6, "duplicates claim 1")]))
    assert 6 in {i["claim_id"] for i in items(rep)} and flagged(rep)[6] == [[1]]
    assert 6 not in {o["claim_id"] for o in rep["omissions"]}


def test_two_requests_left_run_both_and_one_left_runs_the_check_and_displaces_only_the_model_ordering(client, complete_env, world):  # noqa: F811
    full_mesh, full, _ = run(client, complete_env, world, max_model_requests=15)
    assert full_mesh.steps()[-2:] == ["consistency", "synth"] and full["ordering"] == "model"


@pytest.mark.parametrize("cap", [14, 13])
def test_one_request_left_goes_to_the_check_and_the_deterministic_fallback_keeps_the_same_claims_and_warnings(client, complete_env, world, cap):  # noqa: F811
    mesh, rep, _ = run(client, complete_env, world, max_model_requests=cap)
    assert mesh.steps()[-1] == "consistency" and "synth" not in mesh.steps()
    assert rep["ordering"] == "deterministic" and "synthesizer not called" in rep["ordering_reason"]
    assert rep["consistency"]["status"] in ("reviewed", "partial") and rep["consistency"]["has_conflicts"] is True
    assert flagged(rep) == {1: [[6]], 6: [[1]]}  # the warnings do not depend on the synthesizer
    assert {1, 6} <= {i["claim_id"] for i in items(rep)} <= set(rep["consistency"]["shown_claim_ids"])  # both conflicting claims stay; nothing outside the eligible set appears


def test_too_little_output_allowance_skips_only_the_check_and_says_so(client, complete_env, world):  # noqa: F811
    mesh, rep, done = run(client, complete_env, world, max_total_output_tokens=1000)  # the scripted calls report 20 tokens each: fewer than 800 remain at report time
    assert "consistency" not in mesh.steps() and mesh.steps()[-1] == "synth"
    c = rep["consistency"]
    assert c["status"] == "not_checked" and "output tokens" in c["reason"] and c["findings"] == [] and c["has_conflicts"] is False
    assert not flagged(rep) and sorted(i["claim_id"] for i in items(rep)) == sorted(ELIGIBLE)  # the claims are reported unchanged; nothing is labelled without a check
    assert "Status: not_checked" in rep["markdown"]  # 'not checked' is stated, never silent
    assert done["budget_used_of_limit"]["output_tokens"][1] == 1000  # the limit itself is untouched
