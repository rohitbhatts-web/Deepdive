"""Write one run's complete record, read-only, as a Markdown file: the question, status and stop reasons, every event, every model call (usage that a call did not
report stays "unknown", never 0), usage totals, each job's recorded budget, searches, sources and fetch attempts, claims with their verdicts, the report, and the
requirements that remain unanswered. Failures are kept: failed calls, failed fetches, rejected plans, stop reasons.

The database is opened read-only; the output path must not exist. No network, no model call.

    uv run python scripts/run_record.py data/<database>.db --run 1 --out data/<database>.record.md
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import claims as claims_db
from backend import db
from backend import report as report_db

UNKNOWN = "unknown"


def cell(value, limit: int = 300) -> str:
    if value is None:
        return UNKNOWN
    text = str(value).replace("\n", " ").replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def table(headers: list[str], rows: list[list]) -> list[str]:
    if not rows:
        return ["_none recorded_", ""]
    return ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers), *("| " + " | ".join(cell(c) for c in r) + " |" for r in rows), ""]


def mesh_cells(mesh) -> list:
    """Mesh's routing headers as validated at the time: `not recorded` for a call that predates them, `unknown` for a header that was missing or unrecognized."""
    if not isinstance(mesh, dict):
        return ["not recorded"] * 4
    return [cell(mesh.get(k)) for k in ("routing_attempts", "routing_fallback", "provider_latency_ms", "byok_fallback_triggered")]


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def build_record(conn: sqlite3.Connection, run_id: int, source: str = "") -> str:
    run = conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
    if run is None:
        raise ValueError(f"run {run_id} not found")
    out = [f"# Run {run_id} record", "", f"- database: `{source}`" if source else "- database: (not named)", f"- question: {run['question']}",
           f"- created {run['created_at']}, updated {run['updated_at']}",
           f"- collection: **{run['status']}**" + (f" — {run['stop_reason']}" if run["stop_reason"] else ""),
           f"- verification: **{run['verify_status']}**" + (f" — {run['verify_stop_reason']}" if run["verify_stop_reason"] else ""),
           f"- completion: **{run['complete_status']}**" + (f" — {run['complete_stop_reason']}" if run["complete_stop_reason"] else ""), ""]

    events = db.list_events(conn, run_id)
    out += ["## Events", ""]
    rows = []
    for e in events:
        p = e["payload"]
        detail = p.get("problems") or p.get("stop_reason") or p.get("reason") or p.get("status") or ""
        rows.append([e["id"], e["created_at"], e["stage"], e["event_type"], detail if not isinstance(detail, list) else "; ".join(map(str, detail))])
    out += table(["id", "time (UTC)", "stage", "event", "detail"], rows)
    for e in events:
        if e["event_type"] == "plan_rejected" and "rejected_plan" in e["payload"]:
            plan = e["payload"]["rejected_plan"]
            out += [f"### Rejected plan (event {e['id']}, attempt {e['payload'].get('attempt')})", "", "Asks:"]
            out += [f"- {a['label']}: “{a['quote']}”" for a in plan["asks"]]
            out += ["", "Subquestions:"] + [f"- {i}. {q['question']} (covers asks {q['covers']})" for i, q in enumerate(plan["subquestions"])] + [""]

    out += ["## Model calls", "", "A usage figure a call did not report is `unknown`: a failed or timed-out call reports none. It is never shown as 0.", ""]
    calls = conn.execute("SELECT * FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    out += table(["id", "agent", "model", "attempt", "ok", "latency ms", "request id", "prompt", "completion", "total", "finish", "error"],
                 [[c["id"], c["agent"], c["model"], c["attempt"], bool(c["ok"]), c["latency_ms"], c["request_id"], c["prompt_tokens"], c["completion_tokens"], c["total_tokens"], c["finish_reason"], c["error"] or ""] for c in calls])
    note = ("What the transport observed per call (elapsed on a monotonic clock, the configured deadline, phases, HTTP requests actually written, status, safe headers). "
            "A call recorded before diagnostics existed shows `not recorded`, which is not the same as nothing having happened.")
    out += ["### Transport diagnostics", "", note, ""]
    try:
        diagnostics = db.model_call_diagnostics(conn, run_id)
    except sqlite3.OperationalError:  # a database created before the diagnostics table existed
        diagnostics = {}
    rows = []
    for c in calls:
        d = diagnostics.get(c["id"])
        if d is None:
            rows.append([c["id"], "not recorded"] + [""] * 13)
            continue
        t, e = d.get("transport") or {}, d.get("error") or {}
        rows.append([c["id"], d["outcome"], d.get("failure_stage") or "", d["elapsed_ms"], d["deadline_s"], e.get("stopped_in_phase") or t.get("open_phase") or t.get("failed_phase") or "",
                     t.get("http_requests_sent", UNKNOWN), t.get("connect_attempts", UNKNOWN), t.get("status_code") or "", e.get("error_class") or "", json.dumps(t.get("response_headers") or {}), *mesh_cells(t.get("mesh"))])
    out += table(["call", "outcome", "failure stage", "elapsed ms", "deadline s", "stopped in phase", "HTTP requests written", "connect attempts", "status", "error class", "safe headers", "gateway attempts", "routing fallback", "provider latency ms", "BYOK fallback"], rows)
    u = db.model_usage_totals(conn, run_id)
    out += ["### Usage totals", "", f"- model calls: {u['model_calls']} ({u['failed_calls']} failed); usage reported by {u['calls_with_usage']} of {u['model_calls']}",
            f"- prompt tokens: {cell(u['prompt_tokens'])}; completion tokens: {cell(u['completion_tokens'])}; total tokens: {cell(u['total_tokens'])}"
            + ("  (a lower bound: calls without usage are left out)" if 0 < u["calls_with_usage"] < u["model_calls"] else ""), ""]

    out += ["### Job budgets as recorded", "", "Output tokens here are a reservation against the cap (an assumed per-call maximum is counted for every call that reported none), not measured usage.", ""]
    rows = []
    for e in events:
        b = e["payload"].get("budget_used_of_limit")
        if isinstance(b, dict):
            rows.append([e["id"], e["event_type"]] + [f"{b[k][0]} / {b[k][1]}" if k in b else UNKNOWN for k in ("model_requests", "search_queries", "page_fetches", "candidate_claims", "output_tokens", "elapsed_seconds")])
    out += table(["event", "job event", "model requests", "searches", "page fetches", "candidate claims", "output-token cap counted", "elapsed s"], rows)

    out += ["## Searches", ""]
    searches = conn.execute("SELECT id, subquestion_id, query, provider, error FROM searches WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    out += table(["id", "subquestion", "query", "provider", "error"], [[s["id"], s["subquestion_id"], s["query"], s["provider"], s["error"] or ""] for s in searches])
    out += ["## Sources and fetch attempts", ""]
    sources = conn.execute("SELECT s.id, s.final_url, s.title, s.content_type, s.retrieved_at, (SELECT count(*) FROM passages p WHERE p.source_id = s.id) AS passages FROM sources s WHERE s.run_id = ? ORDER BY s.id", (run_id,)).fetchall()
    out += table(["id", "url", "title", "type", "retrieved", "passages"], [[s["id"], s["final_url"], s["title"], s["content_type"], s["retrieved_at"], s["passages"]] for s in sources])
    fetches = conn.execute("SELECT outcome, count(*) AS n FROM fetch_attempts WHERE run_id = ? GROUP BY outcome ORDER BY outcome", (run_id,)).fetchall()
    out += ["Fetch attempts by outcome: " + (", ".join(f"{f['outcome']} {f['n']}" for f in fetches) or "none recorded"), ""]
    failed = conn.execute("SELECT url, outcome, status_code, reason FROM fetch_attempts WHERE run_id = ? AND outcome != 'ok' ORDER BY id", (run_id,)).fetchall()
    if failed:
        out += table(["failed fetch url", "outcome", "status", "reason"], [[f["url"], f["outcome"], f["status_code"], f["reason"]] for f in failed])

    out += ["## Claims", ""]
    claims = claims_db.list_claims(conn, run_id)
    out += table(["id", "v", "status", "integrity", "semantic", "eligible for report", "text"],
                 [[c["id"], c["version"], c["status"], c["integrity_verdict"] or "unchecked", c["semantic_verdict"] or "none", c["eligible_for_report"], c["text"]] for c in claims])

    out += ["## Report and what is still unanswered", ""]
    report = report_db.get_report(conn, run_id)
    if report is None:
        out += ["No report was stored for this run.", ""]
    else:
        out += [f"- outcome: **{report['outcome']}**; stop reason: {report.get('stop_reason') or 'none'}; corrective rounds {report.get('corrective_rounds')}", ""]
        for s in report["sections"]:
            out += [f"### {s['label']}: {s.get('subquestion_text') or ''}"] + [f"- claim {c['claim_id']}: {c['text']}" for c in s["claims"]] + [""]
        out += ["### Unresolved requirements", ""]
        out += table(["requirement", "status", "gap"], [[g["requirement_text"], g["status"], g.get("gap") or ""] for g in report["unresolved_requirements"]])
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("database", type=Path)
    ap.add_argument("--run", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args(argv)
    if args.out.exists():
        print(f"Refusing to overwrite {args.out}: choose a new output path.")
        return 2
    if not args.database.exists():
        print(f"No such database: {args.database}")
        return 2
    conn = open_readonly(args.database)
    try:
        text = build_record(conn, args.run, str(args.database))
    finally:
        conn.close()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    print(f"record written: {args.out} ({len(text.splitlines())} lines)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
