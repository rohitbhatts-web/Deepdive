"""Explicit continuation of a finished correction loop from the evidence already stored (no searching, no fetching).

A run whose completion job FINISHED (or stopped) with requirements still open is never resumed by itself. A person asks for a continuation; it is recorded as an audit
event that carries the previous job's final budget snapshot and stop reason, and the new job runs under its OWN budget with search and fetch limits of zero, so it can only
assess, draft from stored passages the drafter never saw, verify, and report. The earlier job's rows, reports and budget record are never changed. Coverage rounds are unique
per run, so the new job labels its rounds after the stored ones (`round_base`).
"""

import json
import sqlite3

from backend import asks, correction, db, retry
from backend.agents.drafter import stored_candidates
from backend.budget import Budget
from backend.config import Settings


def continuation_settings(settings: Settings) -> Settings:
    """The configured limits with retrieval switched off. Never higher than configured."""
    return retry.retry_settings(settings)


def _previous_job(conn: sqlite3.Connection, run_id: int) -> dict | None:
    done = [e for e in db.list_events(conn, run_id) if e["event_type"] == "completion_finished"]
    return None if not done else {"status": done[-1]["payload"].get("status"), "stop_reason": done[-1]["payload"].get("stop_reason"),
                                  "budget_used_of_limit": done[-1]["payload"].get("budget_used_of_limit")}


def _next_round(conn: sqlite3.Connection, run_id: int) -> int:
    """The first coverage round a new job may use. `latest_round or -1` was wrong when the latest stored round is 0 (0 is falsy): it returned 0 again."""
    latest = correction.latest_round(conn, run_id)
    return 0 if latest is None else latest + 1

def plan(conn: sqlite3.Connection, run_id: int, settings: Settings) -> dict:
    """Whether a continuation is allowed now and exactly what it would do. Offline: no model call, no search, no fetch."""
    run = db.get_run(conn, run_id)
    n_claims = conn.execute("SELECT count(*) FROM claims WHERE run_id = ?", (run_id,)).fetchone()[0]
    rows = correction.assessments(conn, run_id)
    unmet = [a for a in rows if a["status"] != "addressed"]
    reasons = []
    if run["status"] == "researching" or run["verify_status"] == "verifying" or run["complete_status"] == "completing":
        reasons.append("a job is still running for this run")
    elif run["status"] not in ("collection_finished", "stopped"):
        reasons.append(f"collection did not finish (status {run['status']})")
    elif run["complete_status"] not in ("completed", "stopped"):
        reasons.append(f"the last completion job neither finished nor stopped (complete_status {run['complete_status']}); use the retry for a failed job, or start completion")
    elif run["verify_status"] != "completed":
        reasons.append(f"verification is {run['verify_status']}")
    if n_claims == 0:
        reasons.append("no claim exists yet: use the retry (or start completion) instead")
    if not rows:
        reasons.append("no coverage assessment is stored, so there is nothing to continue from")
    elif not unmet:
        reasons.append("every requirement was assessed as addressed: nothing is left to continue")
    s = continuation_settings(settings)
    prov = asks.provenance(conn, run_id)
    tasks = []
    if not reasons:
        probe = lambda sq_id: stored_candidates(conn, run_id, s, sq_id)
        for p in correction.propose_tasks(conn, run_id, rows, s, 0, stored_probe=probe):
            if p["kind"] == "missing_evidence":
                route = p["spec"].get("route", {})
                tasks.append({"requirement_key": p["requirement_key"], "subquestion_id": p["subquestion_id"], "route": route.get("chosen"), "why": route.get("why"),
                              "passage_ids": route.get("passage_ids", []), "skipped": p.get("skip")})
    stored = [t for t in tasks if t["route"] == "stored_evidence" and not t["skipped"]]
    limits = {k: v[1] for k, v in Budget(s).snapshot().items()}
    return {
        "run_id": run_id, "allowed": not reasons, "reasons": reasons, "round_base": _next_round(conn, run_id),
        "unmet_requirements": [a["requirement_key"] for a in unmet], "ask_provenance_recorded": prov["recorded"],
        "ask_provenance_source": prov.get("source"),  # "planner", or "supplied by the operator" (a mapping a person gave for a run planned before it was recorded)
        "ask_priority": correction.ask_priority(conn, run_id, rows)["note"], "tasks": tasks, "previous_job": _previous_job(conn, run_id),
        "new_job_limits": limits,
        "model_request_use": {"cap": limits["model_requests"],
                              "formula": "1 coverage + 1 drafting per stored-evidence task + 1 verification per new claim (at most the candidate cap) + 1 coverage + 1 report synthesis; "
                                         "2 requests are always reserved for coverage and the report",
                              "stored_evidence_tasks_planned": len(stored),
                              "lowest": 2, "highest": min(limits["model_requests"], 1 + len(stored) + limits["candidate_claims"] + 1 + 1)},
        "retrieval": "disabled: search and fetch limits are 0; a task that would need a search is skipped and recorded",
        "cost": {"calculated_usd": None, "why": "unavailable: the repository holds no model price catalog and fetching one needs a provider call; "
                                                 "the hard ceilings are the request, token, time and candidate limits above"},
    }


def start(conn: sqlite3.Connection, run_id: int, settings: Settings) -> dict:
    """Atomically claim the finished run for a continuation and record the lineage event. Raises LookupError(reasons) when not allowed."""
    with db.tx(conn):
        p = plan(conn, run_id, settings)
        if not p["allowed"]:
            raise LookupError("; ".join(p["reasons"]))
        if not db.claim_run_for_continuation(conn, run_id):
            raise LookupError("the run changed state while the continuation was being prepared")
        db.add_event(conn, run_id, "job_continuation_started", "continuation", {
            "round_base": p["round_base"], "previous_job": p["previous_job"], "new_job_limits": p["new_job_limits"],
            "unmet_requirements": p["unmet_requirements"], "tasks": p["tasks"], "note": json.dumps("a person asked for this continuation; no earlier row was changed")})
    return p
