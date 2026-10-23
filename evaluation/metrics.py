"""Metric calculations for the DeepDive evaluation. Pure functions over plain dicts: no database, no model, no network.

Conventions (every function follows them):
- A ratio always carries its numerator and denominator. A zero denominator gives value None and status "N/A", never 0 and never 1.
- Correctness is judged ONLY against `gold`, an authoritative human label. A record whose gold is None is counted as unlabeled and is
  excluded from every correctness metric (it still appears in the totals). The system's own verdict is never used as a gold label.
- Integrity failures, semantic rejections (unsupported / contradicted), partial support and model errors are separate outcomes and are
  never merged into one "rejected" bucket without saying so.
"""

from collections.abc import Iterable, Sequence

VERDICTS = ("supported", "partially_supported", "unsupported", "contradicted")  # the four semantic verdicts (also the gold labels)
NON_JUDGMENTS = ("integrity_failed", "model_error", "not_judged")  # outcomes with no semantic verdict
OUTCOMES = VERDICTS + NON_JUDGMENTS
UNSAFE_GOLD = ("partially_supported", "unsupported", "contradicted")  # a claim that must NOT enter a report as fully supported
REJECT_GOLD = ("unsupported", "contradicted")  # a claim with no support at all


def ratio(numerator: int, denominator: int) -> dict:
    """A ratio with its counts. Zero denominator -> N/A (value None)."""
    if denominator < 0 or numerator < 0 or numerator > denominator:
        raise ValueError(f"invalid ratio {numerator}/{denominator}")
    if denominator == 0:
        return {"value": None, "numerator": numerator, "denominator": 0, "status": "N/A"}
    return {"value": numerator / denominator, "numerator": numerator, "denominator": denominator, "status": "ok"}


def _check(records: Sequence[dict]) -> None:
    ids = [r["id"] for r in records]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate record ids")
    for r in records:
        if r["outcome"] not in OUTCOMES:
            raise ValueError(f"unknown outcome {r['outcome']!r} for {r['id']}")
        if r.get("gold") is not None and r["gold"] not in VERDICTS:
            raise ValueError(f"unknown gold label {r['gold']!r} for {r['id']}")


def confusion_matrix(records: Iterable[dict]) -> dict:
    """Counts of (gold label) x (system outcome) for labelled records. Rows are the four gold labels; columns are the four verdicts plus the
    three non-judgment outcomes, so an integrity failure or a model error is visible as its own column and not hidden inside a verdict."""
    matrix = {g: {o: 0 for o in OUTCOMES} for g in VERDICTS}
    for r in records:
        if r.get("gold") is not None:
            matrix[r["gold"]][r["outcome"]] += 1
    return matrix


def verification_metrics(records: Sequence[dict]) -> dict:
    """Frozen claim-evidence verification metrics. `records`: [{id, gold|None, outcome}].

    Definitions (all over LABELLED records only):
    - supported_precision: of claims the system called `supported`, the share whose gold label is `supported`.
    - supported_recall: of gold-`supported` claims, the share the system called `supported` (so deleting everything scores 0, not 1).
      `_judged_only` excludes gold-supported claims that got no verdict (integrity failure / model error / not judged).
    - unsafe_leakage: of claims whose gold label is partially_supported, unsupported or contradicted, the share the system called `supported`.
    - rejection (gold unsupported or contradicted): `semantic_rejection_recall` counts system unsupported OR contradicted;
      `blocked_from_report_recall` counts anything that is not `supported` (partial, rejection, integrity failure, error) and is a different,
      weaker quantity: it rewards errors. `integrity_blocked` and `model_error` are reported separately.
    - four_verdict_accuracy: exact agreement over labelled records that received a semantic verdict.
    """
    _check(records)
    labelled = [r for r in records if r.get("gold") is not None]
    judged = [r for r in labelled if r["outcome"] in VERDICTS]
    n = len(records)
    out: dict = {
        "label_basis": "authoritative human labels only",
        "counts": {"records": n, "labelled": len(labelled), "unlabelled_excluded": n - len(labelled),
                   "judged": len(judged), **{o: sum(1 for r in labelled if r["outcome"] == o) for o in OUTCOMES},
                   **{f"gold_{g}": sum(1 for r in labelled if r["gold"] == g) for g in VERDICTS}},
        "confusion_matrix": confusion_matrix(labelled),
    }
    called_supported = [r for r in labelled if r["outcome"] == "supported"]
    gold_supported = [r for r in labelled if r["gold"] == "supported"]
    out["supported_precision"] = ratio(sum(1 for r in called_supported if r["gold"] == "supported"), len(called_supported))
    out["supported_recall"] = ratio(sum(1 for r in gold_supported if r["outcome"] == "supported"), len(gold_supported))
    gold_supported_judged = [r for r in gold_supported if r["outcome"] in VERDICTS]
    out["supported_recall_judged_only"] = ratio(sum(1 for r in gold_supported_judged if r["outcome"] == "supported"), len(gold_supported_judged))
    unsafe = [r for r in labelled if r["gold"] in UNSAFE_GOLD]
    out["unsafe_leakage"] = ratio(sum(1 for r in unsafe if r["outcome"] == "supported"), len(unsafe))
    rejects = [r for r in labelled if r["gold"] in REJECT_GOLD]
    rejects_judged = [r for r in rejects if r["outcome"] in VERDICTS]
    out["semantic_rejection_recall"] = ratio(sum(1 for r in rejects if r["outcome"] in REJECT_GOLD), len(rejects))
    out["semantic_rejection_recall_judged_only"] = ratio(sum(1 for r in rejects_judged if r["outcome"] in REJECT_GOLD), len(rejects_judged))
    out["blocked_from_report_recall"] = ratio(sum(1 for r in rejects if r["outcome"] != "supported"), len(rejects))
    out["integrity_blocked"] = ratio(sum(1 for r in labelled if r["outcome"] == "integrity_failed"), len(labelled))
    out["model_error_rate"] = ratio(sum(1 for r in labelled if r["outcome"] == "model_error"), len(labelled))
    out["four_verdict_accuracy"] = ratio(sum(1 for r in judged if r["gold"] == r["outcome"]), len(judged))
    per_class = {}
    for v in VERDICTS:
        said = [r for r in judged if r["outcome"] == v]
        was = [r for r in judged if r["gold"] == v]
        per_class[v] = {"precision": ratio(sum(1 for r in said if r["gold"] == v), len(said)),
                        "recall_judged_only": ratio(sum(1 for r in was if r["outcome"] == v), len(was))}
    out["per_class"] = per_class
    return out


def agreement_with_provisional(records: Sequence[dict]) -> dict:
    """NOT accuracy. Agreement between the system and PROVISIONAL (model-suggested) labels, for sanity checks before human review exists.
    `records`: [{id, proposed, outcome}]. Reported under a name and a basis that say what it is."""
    judged = [r for r in records if r["outcome"] in VERDICTS]
    return {"label_basis": "PROVISIONAL model-suggested labels - not ground truth, not accuracy",
            "agreement": ratio(sum(1 for r in judged if r["proposed"] == r["outcome"]), len(judged)),
            "records": len(records), "not_judged": len(records) - len(judged)}


def paired_filter_comparison(records: Sequence[dict]) -> dict:
    """Before/after on the SAME drafts: accept-everything versus keep-only-what-the-verifier-called-supported.

    Both arms use exactly the same claims, so the difference isolates the verification step. It says nothing about the correction loop (which
    adds new evidence) and nothing about unlabelled claims (excluded from both arms)."""
    _check(records)
    labelled = [r for r in records if r.get("gold") is not None]
    kept = [r for r in labelled if r["outcome"] == "supported"]
    before = ratio(sum(1 for r in labelled if r["gold"] == "supported"), len(labelled))
    after = ratio(sum(1 for r in kept if r["gold"] == "supported"), len(kept))
    wrong_before = sum(1 for r in labelled if r["gold"] != "supported")
    wrong_after = sum(1 for r in kept if r["gold"] != "supported")
    gold_supported = sum(1 for r in labelled if r["gold"] == "supported")
    return {
        "same_claims": len(labelled),
        "precision_before_filter": before, "precision_after_filter": after,
        "precision_change": (after["value"] - before["value"]) if after["value"] is not None and before["value"] is not None else None,
        "not_fully_supported_in_output_before": wrong_before, "not_fully_supported_in_output_after": wrong_after,
        "supported_claims_retained": ratio(sum(1 for r in kept if r["gold"] == "supported"), gold_supported),
        "claims_kept": ratio(len(kept), len(labelled)),
    }


# --- end to end ---------------------------------------------------------------------------------------------------------------------

SEEKING_LABELS = ("found_with_primary_source", "not_found_reported_honestly", "unacceptable")
REQUIREMENT_LABELS = ("addressed", "partially_addressed", "missing", "abstained_appropriately", "abstention_failed")


def completeness(requirement_records: Sequence[dict]) -> dict:
    """Completeness against explicit answer requirements. `requirement_records`: [{question, requirement, gold|None, system_status|None}].

    `gold` is a HUMAN label in REQUIREMENT_LABELS. `system_status` (the coverage assessor's own verdict) is carried through only so the two can
    be compared; it is never used as the correct answer. Unlabelled requirements are excluded and counted."""
    labelled = [r for r in requirement_records if r.get("gold") is not None]
    for r in labelled:
        if r["gold"] not in REQUIREMENT_LABELS:
            raise ValueError(f"unknown requirement label {r['gold']!r}")
    answerable = [r for r in labelled if r["gold"] in ("addressed", "partially_addressed", "missing")]
    abst = [r for r in labelled if r["gold"] in ("abstained_appropriately", "abstention_failed")]
    compared = [r for r in labelled if r.get("system_status") is not None]
    return {
        "label_basis": "authoritative human labels only",
        "requirements": len(requirement_records), "labelled": len(labelled), "unlabelled_excluded": len(requirement_records) - len(labelled),
        "fully_addressed": ratio(sum(1 for r in answerable if r["gold"] == "addressed"), len(answerable)),
        "fully_or_partly_addressed": ratio(sum(1 for r in answerable if r["gold"] != "missing"), len(answerable)),
        "appropriate_abstention": ratio(sum(1 for r in abst if r["gold"] == "abstained_appropriately"), len(abst)),
        "system_assessed_addressed_vs_human": {
            "note": "the coverage assessor's own status, shown for comparison only",
            "agreement_on_addressed": ratio(sum(1 for r in compared if (r["system_status"] == "addressed") == (r["gold"] == "addressed")), len(compared))},
    }


def citation_integrity(citations: Sequence[dict]) -> dict:
    """Deterministic citation checks. `citations`: [{claim_id, passage_exists, quote_found, url_matches, text_matches}] (booleans computed
    from stored data by the collector, never by a model). A citation is `valid` only if all four checks hold; each failure kind is counted
    separately so a missing passage is not confused with a quote that is not in its passage."""
    n = len(citations)
    return {
        "citations": n,
        "valid": ratio(sum(1 for c in citations if all(c[k] for k in ("passage_exists", "quote_found", "url_matches", "text_matches"))), n),
        "failures": {k: sum(1 for c in citations if not c[k]) for k in ("passage_exists", "quote_found", "url_matches", "text_matches")},
    }


def initial_vs_final(initial: Sequence[dict], final: Sequence[dict]) -> dict:
    """Initial drafts versus the final report, by claim lineage. `initial`: [{lineage, outcome, gold|None}] (the first drafting, judged by the
    first verification). `final`: [{lineage, gold|None, from_initial: bool}] (what the report cites).

    `lineage_retention` is the share of initial lineages that still appear in the final report (so a pipeline that deletes everything shows 0).
    Precision figures use human labels only."""
    init_l = {r["lineage"] for r in initial}
    final_l = {r["lineage"] for r in final}
    initial_supported = [r for r in initial if r["outcome"] == "supported"]
    lab_final = [r for r in final if r.get("gold") is not None]
    lab_init = [r for r in initial if r.get("gold") is not None]
    return {
        "initial_claims": len(initial), "initial_called_supported": len(initial_supported), "final_report_claims": len(final),
        "lineage_retention": ratio(len(init_l & final_l), len(init_l)),
        "final_claims_new_since_initial": sum(1 for r in final if r["lineage"] not in init_l),
        "initial_precision_if_all_accepted": ratio(sum(1 for r in lab_init if r["gold"] == "supported"), len(lab_init)),
        "final_report_precision": ratio(sum(1 for r in lab_final if r["gold"] == "supported"), len(lab_final)),
        "final_report_not_fully_supported": sum(1 for r in lab_final if r["gold"] != "supported"),
        "unlabelled_final_claims_excluded": len(final) - len(lab_final),
    }


def abstention_and_retention(questions: Sequence[dict]) -> dict:
    """Behaviour on abstain-expected and answerable questions, so an empty report cannot look like success.
    `questions`: [{id, expected: 'abstain_expected'|answerable-ish, report_claims: int, report_outcome, abstention_gold|None}].
    `abstention_gold` is a human judgement ('appropriate'|'inappropriate') for abstain-expected questions."""
    abst = [q for q in questions if q["expected"] == "abstain_expected"]
    seek = [q for q in questions if q["expected"] == "evidence_seeking"]
    answ = [q for q in questions if q["expected"] not in ("abstain_expected", "evidence_seeking")]
    failed = [q for q in questions if q.get("failed")]
    # a run that failed has no report: that is a failure, never an abstention, so it is excluded from every abstention/seeking judgement below
    judged = [q for q in abst if q.get("abstention_gold") is not None and not q.get("failed")]
    seek_judged = [q for q in seek if q.get("seeking_gold") is not None and not q.get("failed")]
    for q in seek_judged:
        if q["seeking_gold"] not in SEEKING_LABELS:
            raise ValueError(f"unknown evidence-seeking label {q['seeking_gold']!r}")
    return {
        "run_completion": {"questions": len(questions), "completed_with_report": ratio(len(questions) - len(failed), len(questions)),
                           "failed_runs": len(failed), "failed_question_ids": [q.get("id") for q in failed],
                           "failure_classes": {c: sum(1 for q in failed if q.get("failure_class") == c) for c in sorted({q.get("failure_class") or "unclassified" for q in failed})},
                           "note": "a failed run is counted here and nowhere as an abstention or as an answer with no claims"},
        # An evidence-seeking question (the answer may exist or not) is neither answerable nor abstain-expected: it is scored only by a human,
        # who accepts either a figure from a primary source or an honest not-found, and rejects anything else.
        "evidence_seeking_questions": len(seek),
        "evidence_seeking_judged_by_human": len(seek_judged),
        "evidence_seeking_acceptable": ratio(sum(1 for q in seek_judged if q["seeking_gold"] != "unacceptable"), len(seek_judged)),
        "evidence_seeking_found_with_primary_source": ratio(sum(1 for q in seek_judged if q["seeking_gold"] == "found_with_primary_source"), len(seek_judged)),
        "answerable_questions": len(answ),
        "answerable_with_no_report_claims": ratio(sum(1 for q in answ if q["report_claims"] == 0 and not q.get("failed")), len(answ)),
        "answerable_with_insufficient_evidence_outcome": ratio(sum(1 for q in answ if q["report_outcome"] == "insufficient_evidence"), len(answ)),
        "abstain_expected_questions": len(abst),
        "abstain_expected_judged_by_human": len(judged),
        "appropriate_abstention": ratio(sum(1 for q in judged if q["abstention_gold"] == "appropriate"), len(judged)),
        "abstain_expected_with_report_claims": ratio(sum(1 for q in abst if q["report_claims"] > 0), len(abst)),
        "note": "report claims on an abstain-expected question are not wrong by themselves; they must be reviewed (they may be correctly labelled adjacent facts)",
    }


def usage_summary(rows: Sequence[dict]) -> dict:
    """Latency, calls, returned tokens and cost. Each row may have None for anything that was not returned or not stored; a total is
    reported only over the rows that have the field, together with how many did. No total is invented for missing data."""
    def total(key: str) -> dict:
        vals = [r[key] for r in rows if r.get(key) is not None]
        if not vals:
            return {"total": None, "reporting_rows": 0, "of_rows": len(rows), "status": "Unavailable"}
        return {"total": sum(vals), "reporting_rows": len(vals), "of_rows": len(rows), "status": "ok" if len(vals) == len(rows) else "partial"}
    return {"model_calls": total("model_calls"), "failed_calls": total("failed_calls"), "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"), "latency_seconds": total("latency_seconds"),
            "calculated_cost_usd": {**total("calculated_cost_usd"), "meaning": "from returned tokens and a price table; an estimate"},
            "provider_reported_cost_usd": {**total("provider_reported_cost_usd"), "meaning": "charges reported by the provider's usage API"}}
