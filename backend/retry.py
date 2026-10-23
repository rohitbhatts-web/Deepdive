"""Explicit continuation of a failed or stopped job from the evidence already stored (no searching, no fetching).

A failed job is never reset or retried by itself. A person asks for a retry (POST /runs/{id}/retry with an explicit confirmation); the request is
recorded as an audit event that carries the failed job's final budget snapshot and stop reason (the lineage), and the continuation runs under a NEW
job budget whose search and fetch limits are zero, so it cannot retrieve anything: it can only draft, verify, correct from stored evidence and
report. The failed job's own budget record stays exactly as it was written.
"""

import sqlite3

from backend import db
from backend.budget import Budget
from backend.config import Settings

# ONE opt-in drafting profile. It only LOWERS what the drafter is shown and may return; it changes no timeout, model, token limit or retry behaviour.
# Attempt 4 sent the drafter 8,985 characters of passages and allowed 8 claims; that call hit the 60 s limit. The compact profile asks for less
# input and fewer claims. The obligation-aware passage selection still gives every subquestion with a relevant passage its own slot first, so each
# requested subject keeps evidence (with 4 subquestions and 5 claims, at least one claim per subject remains possible).
DRAFTING_PROFILES = {
    "compact": {"max_claims": 5, "draft_input_chars": 5000, "draft_passages_per_subquestion": 2, "draft_context_passages": 0},
}


def retry_settings(settings: Settings, profile: str | None = None) -> Settings:
    """The limits of a continuation: the configured ones, with retrieval switched off. Never higher than the configured limits. An optional named
    drafting profile can only lower drafting limits (min with the configured value); an unknown name is an error."""
    update: dict = {"max_search_queries": 0, "max_page_fetches": 0}
    if profile is not None:
        if profile not in DRAFTING_PROFILES:
            raise ValueError(f"unknown drafting profile {profile!r}; known: {sorted(DRAFTING_PROFILES)}")
        update |= {k: min(getattr(settings, k), v) for k, v in DRAFTING_PROFILES[profile].items()}
    return settings.model_copy(update=update)


def drafting_preview(conn: sqlite3.Connection, run_id: int, settings: Settings, profile: str | None) -> dict:
    """Offline (no model call): what the drafter would be shown from the stored passages, under the configured limits and under the profile."""
    from backend.agents.drafter import select_draft_passages

    def shown(s: Settings) -> dict:
        offered = select_draft_passages(conn, run_id, s)
        return {"limits": {k: getattr(s, k) for k in ("max_claims", "draft_input_chars", "draft_passages_per_subquestion", "draft_context_passages")},
                "subquestions_with_passages": len(offered), "passages": sum(len(v) for v in offered.values()),
                "characters": sum(len(p["text"]) for v in offered.values() for p in v),
                "per_subquestion": {str(k): {"passages": len(v), "characters": sum(len(p["text"]) for p in v)} for k, v in offered.items()}}

    base = retry_settings(settings)
    return {"profile": profile, "configured": shown(base), "with_profile": shown(retry_settings(settings, profile)) if profile else None,
            "note": "profiles only lower limits; timeouts, models and the one-attempt-per-request rule are unchanged"}


def previous_job(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """The most recent job-ending record of the run: its status, stop reason and final budget snapshot (as written when it ended)."""
    for e in reversed(db.list_events(conn, run_id)):
        if e["event_type"] in ("completion_finished", "verification_finished", "research_finished") and e["stage"] in ("graph", "startup"):
            p = e["payload"]
            return {"event_id": e["id"], "event_type": e["event_type"], "finished_at": e["created_at"], "status": p.get("status"),
                    "stop_reason": p.get("stop_reason"), "budget_used_of_limit": p.get("budget_used_of_limit")}
    return None


def _count(conn: sqlite3.Connection, sql: str, run_id: int) -> int:
    return conn.execute(sql, (run_id,)).fetchone()[0]


def plan(conn: sqlite3.Connection, run_id: int, settings: Settings, profile: str | None = None) -> dict:
    """Whether a continuation is allowed now and exactly what it would do. `reasons` lists every blocker in plain words."""
    run = db.get_run(conn, run_id)
    sources = _count(conn, "SELECT count(*) FROM sources WHERE run_id = ?", run_id)
    claims = _count(conn, "SELECT count(*) FROM claims WHERE run_id = ?", run_id)
    passages = _count(conn, "SELECT count(*) FROM passages p JOIN sources s ON s.id = p.source_id WHERE s.run_id = ?", run_id)
    attempts = _count(conn, "SELECT count(*) FROM audit_events WHERE run_id = ? AND event_type = 'job_retry_started'", run_id)
    reasons = []
    if run["status"] == "researching" or run["verify_status"] == "verifying" or run["complete_status"] == "completing":
        reasons.append("a job is still running for this run")
    elif run["status"] not in ("collection_finished", "stopped"):
        reasons.append(f"collection did not finish (status {run['status']}); a retry needs collected evidence")
    elif run["complete_status"] == "completed":
        reasons.append("completion already finished; there is nothing to retry")
    elif run["complete_status"] == "not_started":
        reasons.append("completion has not been started for this run; start it with POST /runs/{id}/complete")
    elif run["verify_status"] not in ("failed", "stopped") or run["complete_status"] not in ("failed", "stopped"):
        reasons.append(f"the last job did not fail or stop (verification {run['verify_status']}, completion {run['complete_status']})")
    if sources == 0:
        reasons.append("no source is stored for this run, so there is no evidence to continue from")
    if claims > 0:
        reasons.append(f"{claims} claim(s) already exist: a retry only drafts from stored evidence when no claim was drafted yet "
                       "(continuing partially verified work is not supported)")
    limits = {k: v[1] for k, v in Budget(retry_settings(settings)).snapshot().items()}
    preview = drafting_preview(conn, run_id, settings, profile) if sources else None
    return {"run_id": run_id, "allowed": not reasons, "drafting": preview, "reasons": reasons, "attempt": attempts + 1,
            "stored_evidence": {"sources": sources, "passages": passages, "claims": claims}, "new_job_limits": limits,
            "retrieval": "disabled: the continuation makes no search and no page fetch (search and fetch limits are 0)",
            "previous_job": previous_job(conn, run_id),
            "note": "Model requests are paid. The limits above are for the NEW job; the failed job's budget record is kept and copied into the "
                    "retry event, never reset."}


def start(conn: sqlite3.Connection, run_id: int, settings: Settings, profile: str | None = None) -> dict:
    """Atomically claim the run for a continuation and record the lineage event. Raises LookupError(reasons) when not allowed."""
    with db.tx(conn):
        p = plan(conn, run_id, settings, profile)
        if not p["allowed"]:
            raise LookupError("; ".join(p["reasons"]))
        if not db.claim_run_for_retry(conn, run_id):
            raise LookupError("the run changed state while the retry was being prepared")
        db.add_event(conn, run_id, "job_retry_started", "api", {
            "attempt": p["attempt"], "confirmed": True, "retry_of": p["previous_job"], "new_job_limits": p["new_job_limits"],
            "retrieval": "disabled", "stored_evidence": p["stored_evidence"], "drafting_profile": profile,
            "budget_note": "a new job budget; the previous job's budget_used_of_limit is copied in retry_of and its own events are unchanged"})
    return p
