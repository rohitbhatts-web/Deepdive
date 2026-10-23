"""Metric calculations of the evaluation harness. All records here are hand-written FIXTURES with hand-computed expected values.
They exercise the arithmetic and the N/A and separation rules; the numbers are NOT project performance."""

import pytest

from evaluation import metrics as m


def rec(i, gold, outcome, **kw):
    return {"id": i, "gold": gold, "outcome": outcome, **kw}


# 12 labelled records. gold -> outcome:
#   supported x4:  3 supported, 1 partially_supported
#   partial x3:    1 supported, 1 partially_supported, 1 integrity_failed
#   unsupported x3: 2 unsupported, 1 supported
#   contradicted x2: 1 contradicted, 1 model_error
FIXTURE = [
    rec("a1", "supported", "supported"), rec("a2", "supported", "supported"), rec("a3", "supported", "supported"),
    rec("a4", "supported", "partially_supported"),
    rec("b1", "partially_supported", "supported"), rec("b2", "partially_supported", "partially_supported"),
    rec("b3", "partially_supported", "integrity_failed"),
    rec("c1", "unsupported", "unsupported"), rec("c2", "unsupported", "unsupported"), rec("c3", "unsupported", "supported"),
    rec("d1", "contradicted", "contradicted"), rec("d2", "contradicted", "model_error"),
]


def test_supported_precision_and_recall_use_the_stated_denominators():
    r = m.verification_metrics(FIXTURE)
    # system said supported: a1 a2 a3 b1 c3 = 5, of which gold supported = 3
    assert r["supported_precision"] == {"value": 0.6, "numerator": 3, "denominator": 5, "status": "ok"}
    # gold supported: a1..a4 = 4, system supported = 3 (a4 was downgraded)
    assert r["supported_recall"]["numerator"] == 3 and r["supported_recall"]["denominator"] == 4
    assert r["supported_recall_judged_only"]["denominator"] == 4  # all four got a verdict


def test_unsafe_leakage_counts_only_gold_non_supported_claims_called_supported():
    r = m.verification_metrics(FIXTURE)
    # gold partial/unsupported/contradicted = 3 + 3 + 2 = 8; called supported: b1, c3 = 2
    assert (r["unsafe_leakage"]["numerator"], r["unsafe_leakage"]["denominator"]) == (2, 8)


def test_rejection_detection_variants_are_kept_apart():
    r = m.verification_metrics(FIXTURE)
    # gold unsupported or contradicted = 5: c1 c2 c3 d1 d2
    assert (r["semantic_rejection_recall"]["numerator"], r["semantic_rejection_recall"]["denominator"]) == (3, 5)  # c1 c2 d1
    # judged only: d2 is a model error, so 4 judged; the same 3 rejected
    assert (r["semantic_rejection_recall_judged_only"]["numerator"], r["semantic_rejection_recall_judged_only"]["denominator"]) == (3, 4)
    # 'blocked from report' is a weaker, different number: everything not called supported (c1 c2 d1 d2)
    assert (r["blocked_from_report_recall"]["numerator"], r["blocked_from_report_recall"]["denominator"]) == (4, 5)
    assert r["blocked_from_report_recall"]["value"] > r["semantic_rejection_recall"]["value"]  # it rewards the model error
    assert r["integrity_blocked"]["numerator"] == 1 and r["model_error_rate"]["numerator"] == 1


def test_four_verdict_accuracy_and_confusion_matrix_separate_non_judgments():
    r = m.verification_metrics(FIXTURE)
    # judged = 10 (b3 integrity_failed and d2 model_error excluded); agree: a1 a2 a3 b2 c1 c2 d1 = 7
    assert (r["four_verdict_accuracy"]["numerator"], r["four_verdict_accuracy"]["denominator"]) == (7, 10)
    cm = r["confusion_matrix"]
    assert cm["supported"] == {"supported": 3, "partially_supported": 1, "unsupported": 0, "contradicted": 0,
                               "integrity_failed": 0, "model_error": 0, "not_judged": 0}
    assert cm["partially_supported"]["integrity_failed"] == 1 and cm["contradicted"]["model_error"] == 1
    assert sum(sum(row.values()) for row in cm.values()) == 12
    assert r["per_class"]["supported"]["precision"]["denominator"] == 5


def test_zero_denominators_are_na_never_zero_or_one():
    empty = m.verification_metrics([])
    for key in ("supported_precision", "supported_recall", "unsafe_leakage", "semantic_rejection_recall", "four_verdict_accuracy", "integrity_blocked"):
        assert empty[key]["value"] is None and empty[key]["status"] == "N/A" and empty[key]["denominator"] == 0
    # nothing judged supported: precision is N/A, not a perfect 1.0
    only_unsupported = m.verification_metrics([rec("x", "supported", "unsupported"), rec("y", "unsupported", "unsupported")])
    assert only_unsupported["supported_precision"]["status"] == "N/A"
    assert only_unsupported["supported_recall"]["value"] == 0.0 and only_unsupported["supported_recall"]["status"] == "ok"
    # no gold-supported claims: recall is N/A
    assert m.verification_metrics([rec("z", "unsupported", "unsupported")])["supported_recall"]["status"] == "N/A"


def test_deleting_everything_cannot_look_successful():
    outcomes = ["not_judged", "model_error", "integrity_failed", "unsupported"]
    records = [rec(f"r{i}", "supported", outcomes[i % 4]) for i in range(8)] + [rec(f"u{i}", "unsupported", "unsupported") for i in range(4)]
    r = m.verification_metrics(records)
    assert r["supported_recall"]["value"] == 0.0  # no supported claim survived
    assert r["supported_precision"]["status"] == "N/A"  # and precision is not reported as 100%
    assert r["unsafe_leakage"]["value"] == 0.0  # the good safety number alone would hide the failure: recall shows it
    pair = m.paired_filter_comparison(records)
    assert pair["supported_claims_retained"]["value"] == 0.0 and pair["claims_kept"]["value"] == 0.0


def test_unlabelled_records_are_excluded_and_never_scored_against_their_own_outcome():
    with_unlabelled = FIXTURE + [rec("u1", None, "supported"), rec("u2", None, "contradicted")]
    a, b = m.verification_metrics(FIXTURE), m.verification_metrics(with_unlabelled)
    assert b["counts"]["records"] == 14 and b["counts"]["labelled"] == 12 and b["counts"]["unlabelled_excluded"] == 2
    for key in ("supported_precision", "supported_recall", "unsafe_leakage", "four_verdict_accuracy"):
        assert a[key] == b[key]
    assert m.verification_metrics([rec("u", None, "supported")])["supported_precision"]["status"] == "N/A"


def test_invalid_input_is_rejected_loudly():
    with pytest.raises(ValueError):
        m.verification_metrics([rec("a", "supported", "maybe")])
    with pytest.raises(ValueError):
        m.verification_metrics([rec("a", "perhaps", "supported")])
    with pytest.raises(ValueError):
        m.verification_metrics([rec("a", "supported", "supported"), rec("a", "supported", "supported")])
    with pytest.raises(ValueError):
        m.ratio(3, 2)


def test_paired_comparison_uses_the_same_claims_for_both_arms():
    p = m.paired_filter_comparison(FIXTURE)
    assert p["same_claims"] == 12
    assert (p["precision_before_filter"]["numerator"], p["precision_before_filter"]["denominator"]) == (4, 12)  # accept everything
    assert (p["precision_after_filter"]["numerator"], p["precision_after_filter"]["denominator"]) == (3, 5)  # keep what was called supported
    assert p["not_fully_supported_in_output_before"] == 8 and p["not_fully_supported_in_output_after"] == 2
    assert p["supported_claims_retained"]["numerator"] == 3 and p["supported_claims_retained"]["denominator"] == 4
    assert p["precision_change"] == pytest.approx(0.6 - 4 / 12)


def test_agreement_with_provisional_labels_is_not_called_accuracy():
    r = m.agreement_with_provisional([{"id": "a", "proposed": "supported", "outcome": "supported"},
                                      {"id": "b", "proposed": "supported", "outcome": "unsupported"},
                                      {"id": "c", "proposed": "unsupported", "outcome": "model_error"}])
    assert (r["agreement"]["numerator"], r["agreement"]["denominator"]) == (1, 2) and r["not_judged"] == 1
    assert "PROVISIONAL" in r["label_basis"] and "not accuracy" in r["label_basis"]


def test_completeness_needs_human_labels_and_does_not_trust_the_system_status():
    none = m.completeness([{"question": "Q1", "requirement": "R1", "gold": None, "system_status": "addressed"}])
    assert none["fully_addressed"]["status"] == "N/A" and none["unlabelled_excluded"] == 1
    rows = [
        {"question": "Q1", "requirement": "R1", "gold": "addressed", "system_status": "addressed"},
        {"question": "Q1", "requirement": "R2", "gold": "partially_addressed", "system_status": "addressed"},  # system over-claims
        {"question": "Q1", "requirement": "R3", "gold": "missing", "system_status": "unresolved"},
        {"question": "Q1", "requirement": "R4", "gold": "addressed", "system_status": None},
        {"question": "Q2", "requirement": "R1", "gold": "abstained_appropriately", "system_status": None},
        {"question": "Q2", "requirement": "R2", "gold": "abstention_failed", "system_status": None},
        {"question": "Q3", "requirement": "R1", "gold": None, "system_status": None},
    ]
    r = m.completeness(rows)
    assert (r["fully_addressed"]["numerator"], r["fully_addressed"]["denominator"]) == (2, 4)  # abstention rows are not in this denominator
    assert (r["fully_or_partly_addressed"]["numerator"], r["fully_or_partly_addressed"]["denominator"]) == (3, 4)
    assert (r["appropriate_abstention"]["numerator"], r["appropriate_abstention"]["denominator"]) == (1, 2)
    assert r["unlabelled_excluded"] == 1
    # the system said 'addressed' for R1 and R2; the human agreed only for R1, so agreement on 'addressed' is 2 of 3 compared rows
    assert (r["system_assessed_addressed_vs_human"]["agreement_on_addressed"]["numerator"],
            r["system_assessed_addressed_vs_human"]["agreement_on_addressed"]["denominator"]) == (2, 3)
    with pytest.raises(ValueError):
        m.completeness([{"question": "Q", "requirement": "R", "gold": "mostly", "system_status": None}])


def test_citation_integrity_counts_each_failure_kind_separately():
    ok = {"claim_id": 1, "passage_exists": True, "quote_found": True, "url_matches": True, "text_matches": True}
    cites = [ok, {**ok, "claim_id": 2, "quote_found": False}, {**ok, "claim_id": 3, "passage_exists": False, "quote_found": False},
             {**ok, "claim_id": 4, "url_matches": False}, {**ok, "claim_id": 5, "text_matches": False}]
    r = m.citation_integrity(cites)
    assert (r["valid"]["numerator"], r["valid"]["denominator"]) == (1, 5)
    assert r["failures"] == {"passage_exists": 1, "quote_found": 2, "url_matches": 1, "text_matches": 1}
    assert m.citation_integrity([])["valid"]["status"] == "N/A"


def test_initial_vs_final_tracks_lineages_and_exposes_deletion():
    initial = [{"lineage": 1, "outcome": "supported", "gold": "supported"}, {"lineage": 2, "outcome": "partially_supported", "gold": "partially_supported"},
               {"lineage": 3, "outcome": "unsupported", "gold": "unsupported"}, {"lineage": 4, "outcome": "supported", "gold": None}]
    final = [{"lineage": 1, "gold": "supported", "from_initial": True}, {"lineage": 2, "gold": "supported", "from_initial": True},
             {"lineage": 9, "gold": "supported", "from_initial": False}, {"lineage": 10, "gold": None, "from_initial": False}]
    r = m.initial_vs_final(initial, final)
    assert (r["lineage_retention"]["numerator"], r["lineage_retention"]["denominator"]) == (2, 4)
    assert r["final_claims_new_since_initial"] == 2 and r["initial_called_supported"] == 2
    assert (r["initial_precision_if_all_accepted"]["numerator"], r["initial_precision_if_all_accepted"]["denominator"]) == (1, 3)
    assert (r["final_report_precision"]["numerator"], r["final_report_precision"]["denominator"]) == (3, 3)
    assert r["unlabelled_final_claims_excluded"] == 1
    gone = m.initial_vs_final(initial, [])
    assert gone["lineage_retention"]["value"] == 0.0 and gone["final_report_precision"]["status"] == "N/A"


def test_abstention_and_retention_cannot_reward_an_empty_report():
    qs = [{"id": "a", "expected": "answerable", "report_claims": 0, "report_outcome": "insufficient_evidence", "abstention_gold": None},
          {"id": "b", "expected": "answerable", "report_claims": 5, "report_outcome": "claims_with_gaps", "abstention_gold": None},
          {"id": "c", "expected": "abstain_expected", "report_claims": 0, "report_outcome": "insufficient_evidence", "abstention_gold": "appropriate"},
          {"id": "d", "expected": "abstain_expected", "report_claims": 3, "report_outcome": "claims_with_gaps", "abstention_gold": "inappropriate"},
          {"id": "e", "expected": "abstain_expected", "report_claims": 1, "report_outcome": "claims_with_gaps", "abstention_gold": None}]
    r = m.abstention_and_retention(qs)
    assert (r["answerable_with_no_report_claims"]["numerator"], r["answerable_with_no_report_claims"]["denominator"]) == (1, 2)
    assert (r["appropriate_abstention"]["numerator"], r["appropriate_abstention"]["denominator"]) == (1, 2)  # e is not yet judged
    assert r["abstain_expected_judged_by_human"] == 2
    assert (r["abstain_expected_with_report_claims"]["numerator"], r["abstain_expected_with_report_claims"]["denominator"]) == (2, 3)
    assert m.abstention_and_retention([])["appropriate_abstention"]["status"] == "N/A"


def test_usage_summary_reports_unavailable_instead_of_zero_and_flags_partial_totals():
    rows = [{"model_calls": 3, "prompt_tokens": 100, "completion_tokens": 10, "latency_seconds": 2.0, "calculated_cost_usd": 0.01},
            {"model_calls": 2, "prompt_tokens": None, "completion_tokens": None, "latency_seconds": None, "calculated_cost_usd": None}]
    u = m.usage_summary(rows)
    assert u["model_calls"] == {"total": 5, "reporting_rows": 2, "of_rows": 2, "status": "ok"}
    assert u["prompt_tokens"]["status"] == "partial" and u["prompt_tokens"]["total"] == 100 and u["prompt_tokens"]["reporting_rows"] == 1
    assert u["provider_reported_cost_usd"]["status"] == "Unavailable" and u["provider_reported_cost_usd"]["total"] is None
    assert m.usage_summary([])["model_calls"]["status"] == "Unavailable"
