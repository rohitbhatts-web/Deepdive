"""ONE bounded inference diagnostic: the real Planner, with its real prompt and schema, on the clean Git question, against a NEW scratch database.

It is prepared, not run automatically. Running it makes one paid model request, so it refuses to start without --confirm-one-paid-request.

What it does, exactly:
  * GET /models once (the same catalog read every real run makes at start), to find out whether the planner model takes a JSON schema;
  * at most ONE chat request to the configured planner model through the shared Mesh client (max_retries=0), using backend.agents.planner.run_planner unchanged;
  * nothing else: no search, no page fetch, no other agent, no research run, no repeat, no fallback model.

Limits (the product's own mechanisms, tightened): max_model_requests=1, so a rejected plan's repair request and an invalid-output retry are refused by the budget
before anything is sent; the per-call output cap max_output_tokens (1,500 unless configured otherwise); a 90 s wall-clock deadline for the request (the default MESH_TIMEOUT); 100 s for
the whole job, so the job's remaining time does not cut the request's deadline short. Both output files are new: the diagnostic refuses a database or report path that already exists, before any network request. It prints, and saves to the
report file, latency, request id, reported token usage (unknown stays unknown), finish reason, outcome and any failure; nothing secret.

    uv run python scripts/planner_diagnostic.py --confirm-one-paid-request --database data/diagnostic-planner-A.db --report data/diagnostic-planner-A.report.log
"""

import json
import sys
from datetime import UTC, datetime
from pathlib import Path

QUESTION = "How do Git merge and Git rebase differ, and when should a developer use each? Use official Git documentation where possible."


def diagnostic_settings(settings):
    return settings.model_copy(update={"max_model_requests": 1, "max_elapsed_seconds": 100.0})


def diagnose(settings, llm, db_path: Path, question: str = QUESTION) -> dict:
    """Run the real planner once against a fresh database and report what was recorded. `llm` is built by the caller (real Mesh, or a fixture in tests)."""
    from backend import db
    from backend.agents.planner import run_planner
    from backend.budget import Budget, BudgetExceeded
    from backend.llm import AgentOutputError, ModelCallError

    db_path = Path(db_path)
    if db_path.exists():
        raise FileExistsError(f"{db_path} already exists: the diagnostic only writes a new database")
    settings = diagnostic_settings(settings)
    conn = db.connect(db_path)
    budget = Budget(settings)
    run = db.create_run(conn, question)
    try:
        run_planner(conn, llm, budget, settings, run)
        outcome = "plan_created"
    except BudgetExceeded as exc:
        outcome = f"stopped by the budget after {budget.model_requests} request: {exc.reason}"
    except (ModelCallError, AgentOutputError) as exc:
        outcome = f"{type(exc).__name__}: {str(exc)[:300]}"
    except Exception as exc:  # noqa: BLE001 - whatever else went wrong is the result; the recorded call rows below are kept either way
        outcome = f"unexpected {type(exc).__name__}: {str(exc)[:300]}"
    calls = [dict(r) for r in conn.execute(
        "SELECT agent, model, attempt, ok, latency_ms, request_id, prompt_tokens, completion_tokens, total_tokens, finish_reason, error FROM model_calls WHERE run_id = ? ORDER BY id",
        (run["id"],))]
    events = [r["event_type"] for r in conn.execute("SELECT event_type FROM audit_events WHERE run_id = ? ORDER BY id", (run["id"],))]
    diagnostics = db.model_call_diagnostics(conn, run["id"])  # what the transport observed per call (phases, HTTP requests written, status, safe headers)
    conn.close()
    return {"outcome": outcome, "model_calls": calls, "diagnostics": diagnostics, "events": events, "budget": budget.snapshot(), "database": str(db_path), "question": question}


def render(result: dict) -> str:
    lines = [f"outcome: {result['outcome']}", f"database (new, scratch): {result['database']}", f"events: {result['events']}"]
    for c in result["model_calls"]:  # None stays None: usage a call did not report is unknown, never 0
        lines.append(f"call: agent={c['agent']} model={c['model']} ok={bool(c['ok'])} latency_ms={c['latency_ms']} request_id={c['request_id']} "
                     f"prompt_tokens={c['prompt_tokens']} completion_tokens={c['completion_tokens']} finish_reason={c['finish_reason']} error={c['error']}")
    for call_id, d in result.get("diagnostics", {}).items():
        lines.append(f"transport diagnostics for call {call_id}: {json.dumps(d, sort_keys=True)}")
    lines.append(f"budget used/limit: {result['budget']}  (output_tokens counts an assumed per-call maximum for a call that reported none: a reservation, not usage)")
    lines.append("pricing: unavailable (the repository holds no price catalog); no cost is calculated")
    return "\n".join(lines)


def _option(argv: list[str], name: str) -> str | None:
    return argv[argv.index(name) + 1] if name in argv and argv.index(name) + 1 < len(argv) else None


def main(argv: list[str]) -> int:
    if "--confirm-one-paid-request" not in argv:
        print("Refusing to run: this makes one paid model request. Re-run with --confirm-one-paid-request to proceed.")
        return 2
    stamp = f"{datetime.now(UTC):%Y%m%dT%H%M%SZ}"
    db_path = Path(_option(argv, "--database") or f"data/diagnostic-planner-{stamp}.db")
    report_path = Path(_option(argv, "--report") or db_path.with_suffix(".report.log"))
    question = _option(argv, "--question") or QUESTION  # the default is the clean Git question with its documentation sentence; pass --question to use another, unchanged
    existing = [p for p in (db_path, report_path) if p.exists()]
    if existing:  # checked before any network request: earlier artifacts are never overwritten
        print(f"Refusing to run: {', '.join(str(p) for p in existing)} already exists. Choose new output paths.")
        return 2
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from backend.config import get_settings
    from backend.llm import LLM
    from backend.mesh_client import get_mesh_async_client, get_mesh_client
    from backend.redact import redact
    from backend.services import check_models, model_catalog

    settings = diagnostic_settings(get_settings())
    try:
        client = get_mesh_client()
        catalog = model_catalog(client)
        check_models(catalog, {settings.planner_model})
    except Exception as exc:  # noqa: BLE001 - nothing paid has happened yet: say what stopped it, without any credential
        print(f"Stopped before any model request: {type(exc).__name__}: {redact(str(exc), settings)[:300]}")
        return 1
    structured = frozenset({settings.planner_model}) if catalog[settings.planner_model].get("supports_structured_output") is True else frozenset()
    llm = LLM(client, settings, structured, async_client_factory=get_mesh_async_client)
    text = render(diagnose(settings, llm, db_path, question))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(text + "\n", encoding="utf-8")
    print(text)
    print(f"report saved: {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
