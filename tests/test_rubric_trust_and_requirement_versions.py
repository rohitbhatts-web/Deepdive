"""(1) Verdicts of an older verifier rubric are history: kept and inspectable, never eligible for a NEW report until re-verified under the current rubric.
(2) Development requirements are versioned: the annotated requirement stays in the original set; results are reported under both sets, never one in place of the other.
Scripted fixtures only."""

import copy
import json

from backend import claims, db
from backend.report import build_report, get_report
from evaluation import datasets, e2e_eval
from tests.loop_helpers import add
from tests.seed import Q0, seed_collected_run


def _record(conn, claim_id, passage, rubric):
    details = {"model_verdict": "supported", "aspects": {}, "unsupported_details": [], "conflicts": [], "evidence_used": [passage], "rubric": rubric}
    with db.tx(conn):
        claims.record_result(conn, claim_id, "semantic", "supported", "fixture", model="m", details=details, passages=[(passage, "claim_evidence")],
                             evidence_ids=[e["id"] for e in claims.claim_evidence(conn, claim_id) if e["passage_id"] == passage])


def _report(conn, run_id):
    info = build_report(conn, run_id, plan=None, ordering_reason="test", model=None, model_call_id=None, stop_reason=None, corrective_rounds=0)
    return get_report(conn, run_id, info["report_id"])


# --- 1. rubric trust ---------------------------------------------------------------------------------------------------------------------

def test_older_rubric_verdicts_are_excluded_from_new_assembly_with_a_clear_reason_but_old_reports_stay_inspectable(conn, settings, monkeypatch):
    r = seed_collected_run(conn)
    cid = add(conn, r, "sq1", Q0, r["p"](0), Q0, settings)
    _record(conn, cid, r["p"](0), "attribution-v2")
    with monkeypatch.context() as mp:  # the time BEFORE the current rubric existed: v2 was what a report was assembled under
        mp.setattr(claims, "SUPPORTED_TRUSTED_RUBRICS", ("attribution-v2", "attribution-v3"))
        assert claims.get_claim(conn, r["run"], cid)["eligible_for_report"] is True
        old = _report(conn, r["run"])
    assert [i["claim_id"] for s in old["sections"] for i in s["claims"]] == [cid] and old["verifier_rubrics"] == ["attribution-v2"]
    # now: the verdict is untouched, the claim is not selectable, and says why
    c = claims.get_claim(conn, r["run"], cid)
    assert c["semantic"]["details"]["rubric"] == "attribution-v2" and c["semantic_verdict"] == "supported"
    assert c["eligible_for_report"] is False and c["needs_rejudge"] is True and c["rubric_current"] is False and c["rubric_trusted"] is False
    assert claims.eligible_claims(conn, r["run"]) == []
    new = _report(conn, r["run"])
    assert [i for s in new["sections"] for i in s["claims"]] == []
    (om,) = [o for o in new["omissions"] if o["claim_id"] == cid]
    assert "attribution-v2" in om["reason"] and claims.CURRENT_RUBRIC in om["reason"] and "has not been re-verified" in om["reason"]
    # the historical report is still there, unchanged, with the rubric its verdict was made under
    again = get_report(conn, r["run"], old["id"])
    assert [i["text"] for s in again["sections"] for i in s["claims"]] == [i["text"] for s in old["sections"] for i in s["claims"]]
    assert again["verifier_rubrics"] == ["attribution-v2"]


def test_a_verdict_under_the_current_rubric_is_eligible_and_its_report_names_that_rubric(conn, settings):
    r = seed_collected_run(conn)
    cid = add(conn, r, "sq1", Q0, r["p"](0), Q0, settings)
    _record(conn, cid, r["p"](0), claims.CURRENT_RUBRIC)
    assert claims.get_claim(conn, r["run"], cid)["eligible_for_report"] is True
    assert _report(conn, r["run"])["verifier_rubrics"] == [claims.CURRENT_RUBRIC]


# --- 2. versioned requirements ----------------------------------------------------------------------------------------------------------

def _q02():
    return next(q for q in datasets.load_questions() if q["id"] == "Q02")


def test_q02_r4_is_annotated_not_edited_and_the_original_set_is_kept():
    q = _q02()
    r4 = next(r for r in q["answer_requirements"] if r["id"] == "R4")
    assert r4["beyond_explicit_question"] is True and "does not mention head-of-line blocking" in r4["annotation"]
    assert r4["text"] == "How each version handles head-of-line blocking, without mixing up the two layers."  # text unchanged
    assert [r["id"] for r in q["answer_requirements"]] == ["R1", "R2", "R3", "R4"]
    assert datasets.requirement_sets(q) == {"requirements-v1": ["R1", "R2", "R3", "R4"], "requirements-v2": ["R1", "R2", "R3"]}
    assert "head-of-line" not in q["question"]
    assert datasets.validate_questions(datasets.load_questions()) == [] and datasets.verify_split_lock() == []  # held-out set untouched


def test_held_out_questions_carry_no_versions_and_the_validator_enforces_the_rules():
    qs = datasets.load_questions()
    assert not any(q.get("requirement_sets") for q in qs if q["split"] == "heldout")
    bad = copy.deepcopy(qs)
    held = next(q for q in bad if q["split"] == "heldout")
    held["requirement_sets"] = {"requirements-v1": [r["id"] for r in held["answer_requirements"]]}
    assert any("held-out questions must not carry requirement versions" in p for p in datasets.validate_questions(bad))
    bad = copy.deepcopy(qs)
    next(q for q in bad if q["id"] == "Q02")["requirement_sets"]["requirements-v2"].append("R4")
    assert any("still contains a requirement annotated" in p for p in datasets.validate_questions(bad))
    bad = copy.deepcopy(qs)
    next(q for q in bad if q["id"] == "Q02")["requirement_sets"]["requirements-v1"] = ["R1", "R2", "R3"]
    assert any("must be the original requirements" in p for p in datasets.validate_questions(bad))


def test_results_are_reported_under_both_requirement_sets_and_the_original_is_not_replaced(tmp_path, monkeypatch, settings):
    labels = tmp_path / "labels.json"
    entries = [{"run_key": "Q02@1", "question_id": "Q02", "requirement_id": rid, "label": lab, "reviewer": "R. Reviewer", "source": "human", "version": 1}
               for rid, lab in (("R1", "addressed"), ("R2", "addressed"), ("R3", "addressed"), ("R4", "missing"))]
    labels.write_text(json.dumps({"claim_labels": [], "requirement_labels": entries, "report_claim_labels": []}), encoding="utf-8")
    monkeypatch.setattr(datasets, "HUMAN", labels)
    outcome = {"question_id": "Q02", "split": "dev", "run_id": 1, "status": "stopped", "stop_reason": "x", "report_outcome": "claims_with_gaps", "report_id": 1,
               "report_claims": 0, "initial": [], "final": [], "citations": [], "system_coverage": [], "corrective_rounds": 1, "budget_used_of_limit": None,
               "usage": {}, "errors": [], "failure": None}
    res = e2e_eval.build_result([outcome], settings, split="dev", executed=False, fixture=True)
    by_set = res.metrics["completeness_by_requirement_set"]
    v1, v2 = by_set["requirements-v1"], by_set["requirements-v2"]
    assert (v1["requirements"], v1["fully_addressed"]["numerator"], v1["fully_addressed"]["denominator"]) == (4, 3, 4)  # R4 counted as originally written
    assert (v2["requirements"], v2["fully_addressed"]["numerator"], v2["fully_addressed"]["denominator"]) == (3, 3, 3)
    assert res.metrics["completeness"]["fully_addressed"] == v1["fully_addressed"]  # the headline figure stays the original set
