"""Complete a run (correction loop + report) and inspect coverage, corrective rounds, revisions and the report."""

import dataclasses
import sqlite3
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from backend import claims as claims_db
from backend import correction, db
from backend import report as report_db
from backend import retry as retry_mod
from backend.api.research import ResearchStarted, _services
from backend.api.runs import RunId, _require_run, get_db
from backend.config import get_settings
from backend.graph import execute_completion
from backend.services import ResearchServices

router = APIRouter()


def get_complete_services() -> ResearchServices:
    """Completing an existing run may run targeted searches, so it needs search and the Researcher as well."""
    return _services("complete")


def get_retry_services() -> ResearchServices:
    """A continuation needs Mesh and the drafter and verifier models, and never search or fetching."""
    return _services("retry")


class RetryRequest(BaseModel):
    confirm_paid_retry: bool = False  # must be true: a retry makes paid model requests
    profile: str | None = None  # optional named drafting profile (lowers drafting limits only); see backend/retry.py DRAFTING_PROFILES


class RetryPlanOut(BaseModel):
    run_id: int
    allowed: bool
    reasons: list[str]  # every blocker, in plain words; empty when allowed
    attempt: int  # 1 for the first retry of this run
    stored_evidence: dict[str, int]  # sources, passages and claims the continuation would start from
    new_job_limits: dict[str, Any]  # the NEW job's limits (search and fetch are 0)
    retrieval: str
    previous_job: dict[str, Any] | None  # the failed job's final status, stop reason and budget snapshot, as stored
    note: str
    drafting: dict[str, Any] | None = None  # offline preview of what the drafter would be shown (configured limits and, if asked, the profile)


class RetryStarted(BaseModel):
    run_id: int
    status: str
    attempt: int
    new_job_limits: dict[str, Any]
    retrieval: str
    previous_job: dict[str, Any] | None


class CompletionStatus(BaseModel):
    run_id: int
    complete_status: str  # not_started | completing | completed | stopped | failed
    stop_reason: str | None  # why the loop ended (its own criteria), a budget limit, or the error
    corrective_rounds: int
    max_corrective_rounds: int
    coverage_round: int | None
    coverage: dict[str, Any] | None  # counts per status and `fully_addressed`; a model assessment, not a guarantee
    report_id: int | None
    report_outcome: str | None
    budget_used_of_limit: dict[str, Any] | None  # as recorded when the job finished


class ObligationOut(BaseModel):
    obligation_key: str  # e.g. Q1.R2.O1: one subject x dimension asked about by the requirement
    subject: str
    dimension: str
    status: str  # addressed | partially_addressed | unresolved
    gap: str
    missing_kind: str
    supporting_claim_ids: list[int]
    notes: dict[str, Any]


class RequirementOut(BaseModel):
    id: int
    round: int
    requirement_key: str
    subquestion_id: int
    requirement_text: str
    status: str  # addressed | partially_addressed | unresolved
    gap: str
    missing_kind: str
    supporting_claim_ids: list[int]
    model: str | None
    notes: dict[str, Any]
    obligations: list[ObligationOut]  # the requirement's status is derived from these


class CoverageOut(BaseModel):
    round: int | None
    summary: dict[str, Any]
    requirements: list[RequirementOut]


class TaskOut(BaseModel):
    id: int
    planned_round: int
    corrective_round: int  # the round in which a planned task runs (planned_round + 1)
    kind: str
    subquestion_id: int | None
    claim_id: int | None
    requirement_key: str | None
    spec: dict[str, Any]
    status: str
    outcome: dict[str, Any]


class RevisionOut(BaseModel):
    claim_id: int
    version: int
    parent_claim_id: int | None
    text: str
    status: str
    integrity_verdict: str | None
    semantic_verdict: str | None
    eligible_for_report: bool
    excluded: dict[str, Any] | None
    superseded_by: int | None
    provenance: dict[str, Any]


class CorrectionsOut(BaseModel):
    rounds: list[dict[str, Any]]
    revisions: list[list[RevisionOut]]  # one list per claim lineage with more than one version or an exclusion
    exclusions: list[dict[str, Any]]


class CitationOut(BaseModel):
    passage_id: str
    role: str
    quotes: list[str]
    source_id: int
    url: str
    title: str | None


class ReportClaimOut(BaseModel):
    claim_id: int
    claim_version: int
    text: str  # the exact stored claim text
    semantic_result_id: int
    verifier_rubric: str | None = None  # the rubric the verdict behind this claim was made under
    role: str | None = None  # direct_answer | relevant_context; None for a report made without relevance judgments
    requirement_keys: list[str] = []
    role_reason: str | None = None
    requirement_texts: list[str] = []  # the requirements this claim directly answers (judged reports)
    cross_reference: dict[str, Any] | None = None  # set when this section shows a claim owned by another section (same verified claim, not a copy)
    conflicts: list[dict[str, Any]] = []  # flagged possible conflicts this claim is part of: [{finding_id, with_claim_ids}]; empty for none and for older reports
    citations: list[CitationOut]


class ReportSectionOut(BaseModel):
    position: int
    label: str
    subquestion_id: int
    subquestion_text: str
    claims: list[ReportClaimOut]  # the direct answers (all claims of a report without relevance judgments)
    context: list[ReportClaimOut] = []  # verified claims that relate to the subquestion but do not answer it
    answered: bool | None = None  # False: no direct answer; None: the report was made without relevance judgments
    open_requirements: list[dict[str, Any]] = []  # this section's requirements that no claim directly answers (judged reports)


class ReportOut(BaseModel):
    id: int
    run_id: int
    outcome: str  # insufficient_evidence | claims_with_gaps | claims_all_requirements_addressed
    fully_addressed: bool
    coverage_round: int | None
    corrective_rounds: int
    stop_reason: str | None
    ordering: str
    ordering_reason: str | None
    model: str | None
    created_at: str
    relevance_judged: bool = False
    verifier_rubrics: list[str] = []
    sections: list[ReportSectionOut]
    requirements: list[dict[str, Any]]
    unresolved_requirements: list[dict[str, Any]]  # assessed as not (fully) answered
    unassessed_requirements: list[dict[str, Any]] = []  # NOT assessed (no coverage assessment covers them): unknown, not unanswered
    coverage_assessed: bool | None = None  # False: the report was built without any coverage assessment
    omissions: list[dict[str, Any]]
    consistency: dict[str, Any] = {"status": "not_recorded", "findings": []}  # cross-claim check: `status` = how much was checked, `findings` = what was flagged (independent)
    markdown: str


@router.post("/runs/{run_id}/complete", response_model=ResearchStarted, status_code=202)
def start_completion(
    run_id: RunId,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    services: ResearchServices = Depends(get_complete_services),
):
    """Take an already collected run through (verification if it has none yet,) the bounded correction loop and the
    report. Needs finished collection with sources; refuses a second start and a run that is still verifying."""
    run = _require_run(conn, run_id)
    with db.tx(conn):
        verify_first = run["verify_status"] == "not_started"
        claimed = db.claim_run_for_completion(conn, run_id, require_collected=True)
        if claimed and verify_first:
            db.claim_run_for_verification(conn, run_id, require_collected=False)
    if not claimed:
        if run["complete_status"] != "not_started":
            raise HTTPException(409, f"completion already started for run {run_id} (complete_status: {run['complete_status']})")
        if run["verify_status"] == "verifying":
            raise HTTPException(409, f"run {run_id} is still being verified")
        raise HTTPException(409, f"run {run_id} has no finished collection with sources (status: {run['status']})")
    background.add_task(execute_completion, run_id, services, verify_first)
    return ResearchStarted(run_id=run_id, status="completing")


@router.get("/runs/{run_id}/retry", response_model=RetryPlanOut)
def retry_plan(run_id: RunId, profile: str | None = None, conn: sqlite3.Connection = Depends(get_db)):
    """What an explicit retry from stored evidence would do and whether it is allowed now. Read-only; nothing starts and no model is called."""
    _require_run(conn, run_id)
    if profile is not None and profile not in retry_mod.DRAFTING_PROFILES:
        raise HTTPException(400, f"unknown drafting profile {profile!r}; known: {sorted(retry_mod.DRAFTING_PROFILES)}")
    return retry_mod.plan(conn, run_id, get_settings(), profile)


@router.post("/runs/{run_id}/retry", response_model=RetryStarted, status_code=202)
def start_retry(
    run_id: RunId,
    body: RetryRequest,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    services: ResearchServices = Depends(get_retry_services),
):
    """Continue a failed or stopped job from the evidence already stored: no search, no fetching, a NEW job budget, lineage recorded.
    Needs `confirm_paid_retry: true`. Never happens automatically."""
    _require_run(conn, run_id)
    if not body.confirm_paid_retry:
        raise HTTPException(400, "confirm_paid_retry must be true: a retry makes paid model requests (see GET /runs/{id}/retry for the limits)")
    if body.profile is not None and body.profile not in retry_mod.DRAFTING_PROFILES:
        raise HTTPException(400, f"unknown drafting profile {body.profile!r}; known: {sorted(retry_mod.DRAFTING_PROFILES)}")
    job_services = dataclasses.replace(services, settings=retry_mod.retry_settings(services.settings, body.profile))
    try:
        p = retry_mod.start(conn, run_id, job_services.settings, body.profile)
    except LookupError as exc:
        raise HTTPException(409, str(exc)) from exc
    background.add_task(execute_completion, run_id, job_services, True)  # verify_first: draft from the stored passages
    return RetryStarted(run_id=run_id, status="completing", attempt=p["attempt"], new_job_limits=p["new_job_limits"], retrieval=p["retrieval"],
                        previous_job=p["previous_job"])


@router.get("/runs/{run_id}/completion", response_model=CompletionStatus)
def completion_status(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    run = _require_run(conn, run_id)
    rows = correction.assessments(conn, run_id)
    report = report_db.get_report(conn, run_id)
    final = next((e for e in reversed(db.list_events(conn, run_id)) if e["event_type"] == "completion_finished"), None)
    from backend.config import get_settings

    return CompletionStatus(
        run_id=run_id, complete_status=run["complete_status"], stop_reason=run["complete_stop_reason"],
        corrective_rounds=report["corrective_rounds"] if report else max(0, (correction.latest_round(conn, run_id) or 0)),
        max_corrective_rounds=get_settings().max_correction_rounds, coverage_round=correction.latest_round(conn, run_id),
        coverage=correction.coverage_summary(rows) if rows else None, report_id=report["id"] if report else None,
        report_outcome=report["outcome"] if report else None,
        budget_used_of_limit=final["payload"].get("budget_used_of_limit") if final else None)


@router.get("/runs/{run_id}/coverage", response_model=CoverageOut)
def coverage(run_id: RunId, round: int | None = None, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    rows = correction.assessments(conn, run_id, round)
    return CoverageOut(round=rows[0]["round"] if rows else None, summary=correction.coverage_summary(rows) if rows else {},
                       requirements=rows)


@router.get("/runs/{run_id}/corrections", response_model=CorrectionsOut)
def corrections(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    tasks = [{**t, "corrective_round": t["planned_round"] + 1} for t in correction.list_tasks(conn, run_id)]
    rounds = []
    for r in sorted({t["planned_round"] for t in tasks}):
        before = correction.assessments(conn, run_id, r)
        after = correction.assessments(conn, run_id, r + 1)
        in_round = [t for t in tasks if t["planned_round"] == r]
        # executed = at least one task really ran; the last assessment also plans work that is then skipped (stop reason)
        executed = any(t["status"] in ("done", "failed") and t["kind"] != "claim_exclusion" for t in in_round)
        rounds.append({"planned_round": r, "corrective_round": r + 1, "executed": executed,
                       "coverage_before": correction.coverage_summary(before) if before else None,
                       "coverage_after": correction.coverage_summary(after) if after else None,
                       "tasks": in_round})
    seen, lineages = set(), []
    for c in claims_db.list_claims(conn, run_id):
        if c["id"] in seen or c["origin"] != "drafted":
            continue
        chain = claims_db.lineage(conn, run_id, c["id"])
        seen |= {x["id"] for x in chain}
        if len(chain) > 1 or chain[-1]["excluded"]:
            lineages.append([{k: x[k] for k in ("version", "parent_claim_id", "text", "status", "integrity_verdict",
                                                "semantic_verdict", "eligible_for_report", "excluded", "superseded_by",
                                                "provenance")} | {"claim_id": x["id"]} for x in chain])
    exclusions = [dict(r) for r in conn.execute("SELECT claim_id, reason, planned_round FROM claim_dispositions WHERE run_id = ? ORDER BY id", (run_id,))]
    return CorrectionsOut(rounds=rounds, revisions=lineages, exclusions=exclusions)


@router.get("/runs/{run_id}/report", response_model=ReportOut)
def latest_report(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    report = report_db.get_report(conn, run_id)
    if report is None:
        raise HTTPException(404, f"no report has been generated for run {run_id}")
    return report


@router.get("/runs/{run_id}/reports/{report_id}", response_model=ReportOut)
def get_report(run_id: RunId, report_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    report = report_db.get_report(conn, run_id, report_id)
    if report is None:
        raise HTTPException(404, f"report {report_id} not found in run {run_id}")
    return report
