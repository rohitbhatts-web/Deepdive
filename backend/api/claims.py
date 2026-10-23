"""Claims, their exact evidence, and verification results. Every lookup is scoped to the run in the URL."""

import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend import claims as claims_db
from backend.api.runs import RunId, _require_run, get_db

router = APIRouter()


class PassageOut(BaseModel):
    id: str
    text: str  # the exact stored passage text the quote must occur in
    start_offset: int
    end_offset: int
    source_id: int
    final_url: str
    title: str | None


class EvidenceOut(BaseModel):
    id: int
    position: int
    passage_ref: str  # what the drafter wrote (may be fabricated)
    passage_id: str | None  # set only when it resolved to a passage of this run
    resolution: str  # resolved | unknown_passage | cross_run_passage
    quote: str
    passage: PassageOut | None


class UsedPassageOut(BaseModel):
    passage_id: str
    role: str  # claim_evidence (cited by the claim) | neighbour_context (relied on but not cited by the claim)


class CitationOut(BaseModel):
    passage_id: str
    role: str
    quotes: list[str]  # the claim's own quote(s) from this passage; empty for neighbour_context
    source_id: int
    url: str
    title: str | None


class ResultOut(BaseModel):
    id: int
    stage: str  # integrity | semantic
    verdict: str  # integrity: passed|failed; semantic: supported|partially_supported|unsupported|contradicted|error
    reason: str
    model: str | None
    model_call_id: int | None
    details: dict[str, Any]
    evidence_ids: list[int]
    passages: list[UsedPassageOut]  # for a semantic verdict: the stored passages it actually relied on
    created_at: str


class ClaimOut(BaseModel):
    id: int
    run_id: int
    subquestion_id: int
    parent_claim_id: int | None
    version: int
    text: str
    origin: str  # drafted | controlled_challenge (a test input, never a natural claim)
    drafter_model: str | None
    draft_call_id: int | None
    provenance: dict[str, Any]
    created_at: str
    status: str  # convenience: names the stage that decided (integrity_failed vs a semantic verdict) - see the two fields below
    integrity_verdict: str | None  # passed | failed | None (not checked). Evidence checks only; never a judgement of support
    semantic_verdict: str | None  # supported | partially_supported | unsupported | contradicted | error | None (not judged)
    superseded_by: int | None  # id of a newer version of this claim, which makes this one stale
    excluded: dict[str, Any] | None  # {reason, planned_round, created_at} when retired by the correction loop (history is kept)
    rubric_current: bool | None  # whether the semantic verdict was made under the current verifier rubric
    needs_rejudge: bool  # a 'supported' verdict from an older rubric: not trusted for reports until re-judged
    eligible_for_report: bool  # the single rule in claims.is_report_eligible; see its docstring
    citations: list[CitationOut]  # what a report may cite: the passages the semantic verdict relied on
    evidence: list[EvidenceOut]
    integrity: ResultOut | None
    semantic: ResultOut | None


class ClaimDetailOut(ClaimOut):
    history: list[ResultOut]  # every stored result for this claim version, oldest first


class VerificationStatus(BaseModel):
    run_id: int
    verify_status: str  # not_started | verifying | completed | stopped | failed
    verify_stop_reason: str | None
    claims: int
    by_status: dict[str, int]
    by_integrity: dict[str, int]  # passed | failed | unchecked
    by_semantic: dict[str, int]  # supported | partially_supported | ... | error | none (never judged)
    eligible_for_report: int
    model_usage_by_agent: dict[str, dict[str, int]]


def _claim_or_404(conn: sqlite3.Connection, run_id: int, claim_id: int) -> dict:
    claim = claims_db.get_claim(conn, run_id, claim_id)  # filters on run_id: another run's claim is a 404
    if claim is None:
        raise HTTPException(404, f"claim {claim_id} not found in run {run_id}")
    return claim


@router.get("/runs/{run_id}/claims", response_model=list[ClaimOut])
def list_claims(run_id: RunId, status: str | None = None, eligible_only: bool = False,
                conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    rows = claims_db.eligible_claims(conn, run_id) if eligible_only else claims_db.list_claims(conn, run_id)
    return [c for c in rows if status is None or c["status"] == status]


@router.get("/runs/{run_id}/claims/{claim_id}", response_model=ClaimDetailOut)
def get_claim(run_id: RunId, claim_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    claim = _claim_or_404(conn, run_id, claim_id)
    return {**claim, "history": claims_db.all_results(conn, run_id, claim_id)}


@router.get("/runs/{run_id}/verification", response_model=VerificationStatus)
def verification_status(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    run = _require_run(conn, run_id)
    usage: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        "SELECT agent, count(*) AS calls, coalesce(sum(ok = 0), 0) AS failed, coalesce(sum(prompt_tokens), 0) AS p,"
        " coalesce(sum(completion_tokens), 0) AS c FROM model_calls WHERE run_id = ?"
        " AND agent IN ('drafter', 'verifier') GROUP BY agent", (run_id,)):
        usage[row["agent"]] = {"calls": row["calls"], "failed_calls": row["failed"],
                               "prompt_tokens": row["p"], "completion_tokens": row["c"]}
    summary = claims_db.summary(conn, run_id)
    return VerificationStatus(
        run_id=run_id, verify_status=run["verify_status"], verify_stop_reason=run["verify_stop_reason"],
        claims=summary["claims"], by_status=summary["by_status"], by_integrity=summary["by_integrity"],
        by_semantic=summary["by_semantic"], eligible_for_report=summary["eligible_for_report"],
        model_usage_by_agent=usage)

