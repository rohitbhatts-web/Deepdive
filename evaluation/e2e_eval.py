"""B. End-to-end research / report evaluation.

`execute` runs the real pipeline (search, fetch, draft, verify, correction loop, report) once per question under one shared per-run Budget and
stores every run in an evaluation database. `collect_outcome` reads a finished run back and computes only what can be computed without
trusting a model: report contents, deterministic citation re-checks, the initial drafts versus the final report, and returned usage.
Completeness against the explicit answer requirements is judged by a HUMAN from review sheets (review.py); the pipeline's own coverage
statuses are carried along for comparison and are never treated as correct.

Validity: this evaluation includes the correction loop, so extra research changes the evidence between initial drafts and the final report.
Use frozen claim-verification (verify_eval) to isolate verification, and `frozen_drafts` (exported here from the SAME initial drafts) to
compare verifier configurations on identical claims.
"""

import json
import re
import sqlite3
import threading
import time
from pathlib import Path

from backend import claims, db
from backend import report as report_db
from backend.config import Settings
from evaluation import metrics
from evaluation.datasets import load_questions, requirement_sets
from evaluation.schemas import E2EQuestionRecord, E2EResult, run_metadata


def plan(split: str, settings: Settings) -> dict:
    """Per-question and total worst-case limits of a live run, from the configured Budget (nothing is executed)."""
    qs = [q for q in load_questions() if split == "all" or q["split"] == split]
    per = {"model_requests": settings.max_model_requests, "search_queries": settings.max_search_queries, "page_fetches": settings.max_page_fetches,
           "output_tokens": settings.max_total_output_tokens, "elapsed_seconds": settings.max_elapsed_seconds}
    return {"split": split, "questions": [q["id"] for q in qs], "per_question_limit": per,
            "total_worst_case": {k: v * len(qs) for k, v in per.items()}}


def _root(conn: sqlite3.Connection, claim_id: int) -> int:
    while True:
        parent = conn.execute("SELECT parent_claim_id FROM claims WHERE id = ?", (claim_id,)).fetchone()[0]
        if parent is None:
            return claim_id
        claim_id = parent


def _first_verdict(conn: sqlite3.Connection, claim_id: int) -> str:
    """The FIRST verification outcome of an initial draft, as it stood before any correction."""
    integrity = conn.execute("SELECT verdict FROM verification_results WHERE claim_id = ? AND stage = 'integrity' ORDER BY id LIMIT 1",
                             (claim_id,)).fetchone()
    if integrity is None:
        return "not_judged"
    if integrity[0] != "passed":
        return "integrity_failed"
    sem = conn.execute("SELECT verdict FROM verification_results WHERE claim_id = ? AND stage = 'semantic' ORDER BY id LIMIT 1", (claim_id,)).fetchone()
    if sem is None:
        return "not_judged"
    return "model_error" if sem[0] == "error" else sem[0]


def _usage(conn: sqlite3.Connection, run_id: int, prices: dict | None) -> dict:
    rows = conn.execute("SELECT agent, model, ok, latency_ms, prompt_tokens, completion_tokens FROM model_calls WHERE run_id = ?", (run_id,)).fetchall()

    def total(col: str):
        vals = [r[col] for r in rows if r[col] is not None]
        return sum(vals) if rows and len(vals) == len(rows) else None

    cost = None
    if prices and rows and all(r["prompt_tokens"] is not None and r["completion_tokens"] is not None and r["model"] in prices for r in rows):
        cost = sum(r["prompt_tokens"] * float(prices[r["model"]]["prompt_usd_per_1m"]) + r["completion_tokens"] * float(prices[r["model"]]["completion_usd_per_1m"])
                   for r in rows) / 1_000_000
    lat = total("latency_ms")
    return {"model_calls": len(rows), "failed_calls": sum(1 for r in rows if not r["ok"]), "prompt_tokens": total("prompt_tokens"),
            "completion_tokens": total("completion_tokens"), "latency_seconds": None if lat is None else lat / 1000,
            "calculated_cost_usd": cost, "provider_reported_cost_usd": None,
            "searches": conn.execute("SELECT count(*) FROM searches WHERE run_id = ?", (run_id,)).fetchone()[0]}


_STAGE_BY_REASON = (("relevance check failed", "collection: source-relevance check (cross-subquestion association)"),
                    ("researcher failed", "collection: research of a subquestion"), ("drafter failed", "verification: drafting"),
                    ("insufficient time", "model request refused: not enough job time left"))


def failure_info(conn: sqlite3.Connection, run_id: int) -> dict | None:
    """Why a run produced no report, from its stored records only. None when the run completed with a report. The classification is a deterministic
    reading of the failed call's measured wait against its own deadline: `environment_interrupted_suspected` means the wait was far beyond the deadline,
    which only a stall of the host (sleep/standby, suspended process) explains; it is a suspicion to confirm against the operating system's logs, not a proof."""
    run = db.get_run(conn, run_id)
    report = report_db.get_report(conn, run_id)
    if run is None or (report is not None and "failed" not in (run["status"], run["verify_status"], run["complete_status"])):
        return None
    reason = run["stop_reason"] or run["complete_stop_reason"] or ""
    stage = next((label for key, label in _STAGE_BY_REASON if key in reason), None)
    last = conn.execute("SELECT event_type FROM audit_events WHERE run_id = ? AND event_type NOT IN ('research_finished','verification_skipped','verification_finished','completion_finished') ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    call = conn.execute("SELECT agent, model, latency_ms, error FROM model_calls WHERE run_id = ? AND ok = 0 ORDER BY id DESC LIMIT 1", (run_id,)).fetchone()
    cls, detail = "application_or_unknown", {}
    if call is not None:
        err = call["error"] or ""
        m = re.search(r"request timeout (\d+)s", err)
        deadline = int(m.group(1)) if m else None
        detail = {"agent": call["agent"], "model": call["model"], "measured_wait_ms": call["latency_ms"], "deadline_seconds": deadline, "error": err[:300]}
        if "invalid output" in err:
            cls = "validation_failure"
        elif "Timeout" in err and deadline and call["latency_ms"] is not None:
            cls = "environment_interrupted_suspected" if call["latency_ms"] / 1000 > 3 * deadline + 30 else "transport_timeout"
        elif "Timeout" in err:
            cls = "transport_timeout"
        else:
            cls = "provider_or_transport_error"
    elif reason.startswith("max_") or "budget" in reason or "insufficient" in reason:
        cls = "budget_exhaustion"
    return {"status": run["status"], "stage": stage or (f"after event {last['event_type']}" if last else "unknown"), "reason": reason,
            "classification": cls, "last_failed_call": detail or None, "report_stored": report is not None}


def collect_outcome(conn: sqlite3.Connection, run_id: int, question: dict | None = None, *, prices: dict | None = None) -> dict:
    """Everything computable from a finished run's stored data. `question` supplies id and split when the run belongs to the question set."""
    run = db.get_run(conn, run_id)
    report = report_db.get_report(conn, run_id)
    all_claims = claims.list_claims(conn, run_id)
    initial_rows = [c for c in all_claims if c["origin"] == "drafted" and c["parent_claim_id"] is None
                    and not (c["provenance"] or {}).get("candidate")]
    initial = [{"lineage": c["id"], "claim_id": c["id"], "text": c["text"], "outcome": _first_verdict(conn, c["id"]), "gold": None}
               for c in initial_rows]
    initial_lineages = {r["lineage"] for r in initial}
    final, citations = [], []
    for section in (report["sections"] if report else []):
        for item in section["claims"]:
            root = _root(conn, item["claim_id"])
            stored = claims.get_claim(conn, run_id, item["claim_id"])
            final.append({"lineage": root, "claim_id": item["claim_id"], "text": item["text"], "gold": None, "from_initial": root in initial_lineages})
            for ct in item["citations"]:
                p = conn.execute("SELECT p.text, s.final_url, s.run_id FROM passages p JOIN sources s ON s.id = p.source_id WHERE p.id = ?",
                                 (ct["passage_id"],)).fetchone()
                exists = p is not None and p["run_id"] == run_id
                citations.append({
                    "claim_id": item["claim_id"], "passage_id": ct["passage_id"], "role": ct["role"], "passage_exists": exists,
                    "quote_found": bool(exists and all(claims.find_quote(p["text"], q) is not None for q in ct["quotes"])),
                    "url_matches": bool(exists and p["final_url"] == ct["url"]),
                    "text_matches": bool(stored and stored["text"] == item["text"] and stored["version"] == item["claim_version"])})
    system_cov = []
    from backend import correction
    for r in correction.assessments(conn, run_id):
        system_cov.append({"requirement_key": r["requirement_key"], "text": r["requirement_text"], "status": r["status"], "gap": r["gap"]})
    done = next((e for e in reversed(db.list_events(conn, run_id)) if e["event_type"] == "completion_finished"), None)
    errors = []
    if report is None:
        errors.append("no report was stored")
    return {
        "question_id": question["id"] if question else None, "split": question["split"] if question else "unknown", "run_id": run_id,
        "status": run["status"] if run["complete_status"] in ("not_started", "completing") else run["complete_status"],
        "stop_reason": run["complete_stop_reason"] or run["stop_reason"], "report_outcome": report["outcome"] if report else None,
        "report_id": report["id"] if report else None, "report_claims": len(final), "report_markdown": report["markdown"] if report else None,
        "initial": initial, "final": final, "citations": citations, "system_coverage": system_cov,
        "corrective_rounds": report["corrective_rounds"] if report else None,
        "budget_used_of_limit": done["payload"].get("budget_used_of_limit") if done else None,
        "usage": _usage(conn, run_id, prices), "errors": errors, "failure": failure_info(conn, run_id),
    }


def frozen_drafts(conn: sqlite3.Connection, run_id: int, example_prefix: str) -> list[dict]:
    """The INITIAL drafts of a run in the claim-evidence dataset format, for re-verifying the very same claims with another verifier
    configuration (the paired comparison). Labels start empty and are provisional-free: nothing here is a proposed or human label."""
    out = []
    for c in claims.list_claims(conn, run_id):
        if c["origin"] != "drafted" or c["parent_claim_id"] is not None or (c["provenance"] or {}).get("candidate"):
            continue
        ev = []
        for e in c["evidence"]:
            if e["passage"] is None:
                continue
            s = conn.execute("SELECT retrieved_at, content_hash FROM sources WHERE id = ?", (e["passage"]["source_id"],)).fetchone()
            ev.append({"passage_text": e["passage"]["text"], "quote": e["quote"], "url": e["passage"]["final_url"], "title": e["passage"]["title"],
                       "retrieved_at": s["retrieved_at"], "content_hash": s["content_hash"], "source_key": f"s{e['passage']['source_id']}",
                       "ordinal": int(e["passage"]["id"].rsplit("P", 1)[1])})
        sq = conn.execute("SELECT question FROM subquestions WHERE id = ?", (c["subquestion_id"],)).fetchone()[0]
        out.append({"id": f"{example_prefix}-C{c['id']}", "split": "dev", "claim": c["text"], "subquestion": sq, "evidence": ev, "tags": ["frozen_initial_draft"],
                    "synthetic": None, "ambiguity": None, "proposed": {"label": "unsupported", "status": "provisional", "proposed_by": "none (placeholder)", "rationale": ""}})
    return out


def build_result(outcomes: list[dict], settings: Settings, *, split: str, executed: bool, fixture: bool, notes: list[str] | None = None) -> E2EResult:
    questions = {q["id"]: q for q in load_questions()}
    from evaluation.datasets import (
        authoritative_report_claim_labels,
        authoritative_requirement_labels,
    )
    req_labels = authoritative_requirement_labels()
    claim_labels = authoritative_report_claim_labels()
    for o in outcomes:
        key = f"{o['question_id']}@{o['run_id']}"
        for r in o["final"]:
            r["gold"] = claim_labels.get((key, r["claim_id"]))
    req_records, by_set = [], {}
    for o in outcomes:
        q = questions.get(o["question_id"])
        for r in (q["answer_requirements"] if q else []):
            rec = {"question": o["question_id"], "requirement": r["id"], "system_status": None,
                   "gold": req_labels.get((f"{o['question_id']}@{o['run_id']}", o["question_id"], r["id"]))}
            req_records.append(rec)  # the original requirement set: every requirement, as first written
            for name, members in requirement_sets(q).items():
                if r["id"] in members:
                    by_set.setdefault(name, []).append(rec)
    qrows = [{"id": o["question_id"], "expected": questions[o["question_id"]]["expected_outcome"] if o["question_id"] in questions else "answerable",
              "report_claims": o["report_claims"], "report_outcome": o["report_outcome"], "abstention_gold": None, "seeking_gold": None,
              "failed": bool(o.get("failure")), "failure_class": (o.get("failure") or {}).get("classification")} for o in outcomes]
    all_initial = [dict(r, lineage=f"{o['run_id']}:{r['lineage']}") for o in outcomes for r in o["initial"]]
    all_final = [dict(r, lineage=f"{o['run_id']}:{r['lineage']}") for o in outcomes for r in o["final"]]
    labelled = any(r["gold"] is not None for r in req_records) or any(r["gold"] is not None for r in all_final)
    result_metrics = {
        "completeness": metrics.completeness(req_records),  # == requirements-v1, the original set; never replaced
        "completeness_by_requirement_set": {name: {"requirement_set": name, **metrics.completeness(recs)} for name, recs in sorted(by_set.items())},
        "citation_integrity": metrics.citation_integrity([c for o in outcomes for c in o["citations"]]),
        "initial_vs_final": metrics.initial_vs_final(all_initial, all_final),
        "abstention_and_retention": metrics.abstention_and_retention(qrows),
        "usage": metrics.usage_summary([o["usage"] for o in outcomes]),
        "reports_stored": metrics.ratio(sum(1 for o in outcomes if o["report_id"]), len(outcomes)),
        "questions": len(outcomes),
    }
    meta = (
        run_metadata("e2e", settings, split=split, budget={"per_run": {"model_requests": settings.max_model_requests,
                              "search_queries": settings.max_search_queries, "page_fetches": settings.max_page_fetches,
                              "output_tokens": settings.max_total_output_tokens, "elapsed_seconds": settings.max_elapsed_seconds,
                              "candidate_claims": settings.max_candidate_claims, "correction_rounds": settings.max_correction_rounds}},
                              run_ids=[o["run_id"] for o in outcomes if o["run_id"]], executed=executed, fixture=fixture,
                              label_basis="authoritative-human" if labelled else "none",
                              notes=(notes or []) + ([] if labelled else ["no authoritative human labels: completeness and report-claim precision are N/A"])))
    return E2EResult(
        metadata=meta,
        questions=[E2EQuestionRecord(**{k: v for k, v in o.items() if k in E2EQuestionRecord.model_fields}) for o in outcomes],
        metrics={"label_statement": meta.label_statement, **result_metrics}, errors=[f"{o['question_id']}: {e}" for o in outcomes for e in o["errors"]])


STAGE_EVENTS = {"plan_created", "research_started", "subquestion_done", "research_finished", "claims_drafted", "integrity_checked", "claims_verified",
                "coverage_assessed", "correction_round_started", "correction_round_finished", "report_created", "completion_finished", "verification_finished",
                "drafting_failed", "job_retry_started"}


class Progress(threading.Thread):
    """Concise, flushed progress for the CLI. A daemon thread that only READS the evaluation database (read-only connection) and prints one line per
    stage event plus a heartbeat every `heartbeat` seconds; it never writes and cannot affect the run."""

    def __init__(self, db_path: Path, run_id: int, label: str, *, interval: float = 3.0, heartbeat: float = 30.0, say=None):
        super().__init__(daemon=True)
        self.db_path, self.run_id, self.label = db_path, run_id, label
        self.interval, self.heartbeat = interval, heartbeat
        self.say = say or (lambda msg: print(msg, flush=True))
        self.stop_event = threading.Event()
        self.seen = 0
        self.started = time.monotonic()

    def _counts(self, conn: sqlite3.Connection) -> str:
        n = {t: conn.execute(f"SELECT count(*) FROM {t} WHERE run_id = ?", (self.run_id,)).fetchone()[0] for t in ("model_calls", "searches", "sources", "claims")}
        return f"model calls {n['model_calls']}, searches {n['searches']}, sources {n['sources']}, claims {n['claims']}"

    def poll(self, last_print: float) -> float:
        conn = sqlite3.connect(f"file:{self.db_path.as_posix()}?mode=ro", uri=True, timeout=5)
        try:
            rows = conn.execute("SELECT id, event_type FROM audit_events WHERE run_id = ? AND id > ? ORDER BY id", (self.run_id, self.seen)).fetchall()
            self.seen = rows[-1][0] if rows else self.seen
            stages = [r[1] for r in rows if r[1] in STAGE_EVENTS]
            now = time.monotonic()
            if stages or now - last_print >= self.heartbeat:
                done = conn.execute("SELECT count(*) FROM subquestions WHERE run_id = ? AND status = 'evidence_collected'", (self.run_id,)).fetchone()[0]
                total = conn.execute("SELECT count(*) FROM subquestions WHERE run_id = ?", (self.run_id,)).fetchone()[0]
                what = ", ".join(dict.fromkeys(stages)) or "working"
                self.say(f"[{self.label}] {int(now - self.started)}s: {what} | subquestions {done}/{total} | {self._counts(conn)}")
                return now
            return last_print
        finally:
            conn.close()

    def run(self) -> None:
        last = time.monotonic()
        while not self.stop_event.wait(self.interval):
            try:
                last = self.poll(last)
            except sqlite3.Error:
                continue  # a locked or not-yet-written database: try again at the next tick

    def finish(self) -> None:
        self.stop_event.set()
        self.join(timeout=10)


def _write_partial(path: Path, completed: list[dict], interrupted: dict | None, status: str) -> None:
    path.with_suffix(".partial.json").write_text(
        json.dumps({"status": status, "completed_questions": completed, "interrupted_question": interrupted}, indent=1, default=str), encoding="utf-8")


def execute(split: str, settings: Settings, db_path: Path, out_path: Path, *, confirm: bool, question_ids: list[str] | None = None,
            say=None) -> None:
    """LIVE: one real end-to-end run per question. Refuses unless `confirm` is True. Spends model, search and fetch credits.

    Progress is printed (flushed) and each finished question is written to `<out>.partial.json` at once, so a later interruption keeps it. If the process is
    interrupted (KeyboardInterrupt etc.) the question in flight is collected best-effort as `interrupted_question`, the partial file says `interrupted`,
    and the interrupt is re-raised."""
    if not confirm:
        raise SystemExit("refusing to run live: pass --execute (this spends model, search and fetch credits)")
    from backend.graph import execute_research
    from backend.services import build_services

    say = say or (lambda msg: print(msg, flush=True))
    # The run is created in `db_path`, so the job (svc.open_db -> settings.database_path) must open the SAME file. Without this the job opened the
    # application's default database, where the run id does not exist: the first event write failed on its foreign key.
    settings = settings.model_copy(update={"database_path": str(db_path)})
    svc = build_services(settings, mode="complete")
    questions = [q for q in load_questions() if (split == "all" or q["split"] == split) and (question_ids is None or q["id"] in question_ids)]
    say(f"end-to-end evaluation: {len(questions)} question(s) {[q['id'] for q in questions]}; database {db_path.name}; limits "
        f"{settings.max_model_requests} requests, {settings.max_search_queries} searches, {settings.max_page_fetches} fetches, "
        f"{settings.max_total_output_tokens} output tokens, {settings.max_elapsed_seconds:g}s per question")
    outcomes: list[dict] = []
    for n, q in enumerate(questions, 1):
        conn = db.connect(db_path)
        run_id = db.create_run(conn, q["question"])["id"]
        with db.tx(conn):
            db.claim_run_for_research(conn, run_id)
            db.claim_run_for_verification(conn, run_id, require_collected=False)
            db.claim_run_for_completion(conn, run_id, require_collected=False)
        conn.close()
        probe = svc.open_db()  # read-only check before any provider call: the committed run must be visible to the job's own connection
        try:
            visible = db.get_run(probe, run_id) is not None
        finally:
            probe.close()
        if not visible:
            raise RuntimeError(f"run {run_id} created in {db_path} is not visible to the job's database ({svc.settings.database_path}); nothing was sent")
        say(f"[{q['id']}@{run_id}] question {n}/{len(questions)} started")
        progress = Progress(db_path, run_id, f"{q['id']}@{run_id}", say=say)
        progress.start()
        try:
            execute_research(run_id, svc, verify=True, complete=True)  # one shared Budget for this run, as in production
        except BaseException:
            progress.finish()
            try:
                conn = db.connect(db_path)
                interrupted = collect_outcome(conn, run_id, q)
                conn.close()
            except Exception:  # noqa: BLE001 - best effort: the original interrupt/error is what must propagate
                interrupted = {"question_id": q["id"], "run_id": run_id, "errors": ["could not collect the in-flight question"]}
            _write_partial(out_path, outcomes, interrupted, "interrupted")
            say(f"[{q['id']}@{run_id}] INTERRUPTED; {len(outcomes)} completed question(s) kept in {out_path.with_suffix('.partial.json').name}")
            raise
        progress.finish()
        conn = db.connect(db_path)
        outcomes.append(collect_outcome(conn, run_id, q))
        conn.close()
        _write_partial(out_path, outcomes, None, "in_progress")
        o = outcomes[-1]
        say(f"[{q['id']}@{run_id}] finished: status {o['status']}, report {o['report_outcome']}, {o['report_claims']} report claim(s); saved to "
            f"{out_path.with_suffix('.partial.json').name}")
    result = build_result(outcomes, settings, split=split, executed=True, fixture=False)
    out_path.write_text(result.model_dump_json(indent=1), encoding="utf-8")
    _write_partial(out_path, outcomes, None, "complete")
    say(f"done; result written to {out_path.name}")
