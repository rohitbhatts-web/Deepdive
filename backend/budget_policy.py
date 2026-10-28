"""How a job's ONE model-request budget is shared between optional and required work. The total limit (max_model_requests) never changes.

Why this exists: before it, the only reservation (graph.RESERVE) was consulted inside the correction loop, AFTER the first coverage assessment. Planning,
research, relevance, drafting, verification (including validation retries) and re-judging could spend the whole budget first. A run that collects, drafts,
verifies and assesses in one 20-request job typically spends 11 requests before verification (planner 1, researcher 8, relevance 1, drafter 1); verifying 8
claims needs 8 more and the final assessment at least 3 (obligations, coverage, consistency), so the end-to-end workflow did not fit, and Run 2 of 27 Oct 2026
ran out during verification with no coverage assessment at all.

The policy, enforced by Budget.charge_model_request for every request attempt (so no stage can bypass it):
- REQUIRED work is the final assessment: deriving obligations (once per run), the coverage assessment and the cross-claim consistency check. It runs inside
  `budget.required()` and may use held capacity.
- Everything else is OPTIONAL and may only spend what is not held: planning, research, relevance, drafting, verification, re-judging, corrections,
  re-verification, and synthesis (which falls back to deterministic ordering).
- The hold is set per phase (`hold_for`): during collection it covers the final assessment, one drafting request and verifying at least one claim per
  subquestion; during drafting and verification it covers the final assessment; drafting is capped at the claims that can still be verified, with one request
  of slack for a validation retry or failure. Reaching the hold ends that optional stage (ReserveReached) and the job moves on to the required work; it never
  skips verification of what was verified, never marks an unverified claim supported, and the curtailment is recorded and reported.
- Job TIME is held for the final assessment too (FINAL_CALL_SECONDS per held final-assessment request): an optional call may not start, or run, into it.
  OUTPUT TOKENS are not reserved: they stop the job wherever they run out, and the report is then built deterministically and says what was not assessed or
  not checked.
"""

import sqlite3

from backend import correction, db
from backend.budget import Budget
from backend.config import Settings

COVERAGE_REQUESTS = 1  # one coverage assessment call
CONSISTENCY_REQUESTS = 1  # one cross-claim consistency call
DRAFT_REQUESTS = 1  # one drafting call
RETRY_SLACK = 1  # requests kept free when capping drafting, for a validation retry or a failed verification
# Seconds of job time held per final-assessment request. The slowest final-assessment call in the preserved records took 10.5 s (coverage 2.2-9.0 s,
# consistency 7.1 and 10.5 s); 15 s leaves margin, and every call also needs `min_call_seconds` left to start. An ESTIMATE from 7 recorded calls, not a guarantee.
FINAL_CALL_SECONDS = 15.0


def obligations_needed(conn: sqlite3.Connection, run_id: int) -> int:
    """1 when some requirement still has no coverage obligations (they are derived in one request, the first time coverage runs), else 0."""
    have = {o["requirement_key"] for o in correction.obligations(conn, run_id)}
    return int(any(r["key"] not in have for r in correction.requirements(conn, run_id)))


def final_assessment_requests(conn: sqlite3.Connection, run_id: int) -> int:
    return obligations_needed(conn, run_id) + COVERAGE_REQUESTS + CONSISTENCY_REQUESTS


def verification_floor(conn: sqlite3.Connection, run_id: int) -> int:
    """During collection, keep room to verify at least one claim per subquestion (at least 1 before the plan exists)."""
    return max(1, len(db.list_subquestions(conn, run_id)))


def hold_for(phase: str, conn: sqlite3.Connection, run_id: int, *, complete: bool, verify: bool) -> tuple[int, str, float]:
    """(requests to hold, what for, seconds of job time to hold) for an OPTIONAL phase of a job. `complete`: this job will make the final assessment; `verify`:
    it will draft and verify. Time is held only for the final assessment (FINAL_CALL_SECONDS per request); drafting and verification time is not held."""
    final = final_assessment_requests(conn, run_id) if complete else 0
    seconds = final * FINAL_CALL_SECONDS
    if phase == "collection":
        if not verify:
            return 0, "", 0.0
        floor = verification_floor(conn, run_id)
        why = f"drafting ({DRAFT_REQUESTS}) and verifying at least {floor} claim(s)" + (f", and the final assessment ({final} requests, {seconds:g}s)" if final else "")
        return final + DRAFT_REQUESTS + floor, why, seconds
    if phase in ("drafting", "verification", "corrections"):
        return final, (f"the final assessment ({final} requests, {seconds:g}s: obligations, coverage and the consistency check)" if final else ""), seconds
    if phase == "report":
        return 0, "", 0.0
    raise ValueError(f"unknown phase {phase!r}")


def drafting_cap(budget: Budget, settings: Settings) -> int:
    """How many claims drafting may produce so that every one of them can still be verified within what optional work may spend (0 = do not draft)."""
    capacity = budget.optional_left() - DRAFT_REQUESTS  # verifications affordable after the drafting request
    if capacity <= 0:
        return 0
    return min(settings.max_claims, capacity - RETRY_SLACK if capacity > RETRY_SLACK else capacity)
