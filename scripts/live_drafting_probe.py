"""ONE drafting request on a COPY of a failed run's stored evidence (no search, no fetch, no verification, no retry).

It uses the production path exactly: `build_services` (real Mesh client and catalog check), `run_drafter` (the production passage selection, prompt, structured
output and 1,500-token limit) and the production client timeout (`MESH_TIMEOUT`, 90 s by default). The only extra limits are a job budget of ONE model request (so a second attempt after an
invalid reply cannot be sent) and 3,000 output tokens. Latency is measured here and so is recorded even when the call fails; usage the provider did not return stays
unknown. Never prints credentials.

    uv run python scripts/live_drafting_probe.py SOURCE_DB COPY_DB            # prints the plan and exits (no call)
    uv run python scripts/live_drafting_probe.py SOURCE_DB COPY_DB --execute  # LIVE: exactly one request
"""

import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import db
from backend.agents.drafter import SYSTEM, run_drafter, select_draft_passages
from backend.budget import Budget, BudgetExceeded
from backend.config import get_settings
from backend.llm import AgentOutputError, ModelCallError
from backend.mesh_client import get_mesh_client
from backend.services import build_services


def balance() -> str:
    try:
        d = get_mesh_client().get("/balance", cast_to=object)
        return f"available ${float(d['available_usd']):.4f} (service updated_at {d.get('updated_at')})"
    except Exception as exc:  # noqa: BLE001
        return f"Unavailable ({type(exc).__name__})"


def main() -> int:
    source, copy = Path(sys.argv[1]), Path(sys.argv[2])
    execute = "--execute" in sys.argv
    if copy.exists():
        sys.exit(f"{copy} exists; pass a new path (the probe never reuses or overwrites a database)")
    for suffix in ("", "-wal", "-shm"):
        if Path(str(source) + suffix).exists():
            shutil.copy2(str(source) + suffix, str(copy) + suffix)
    settings = get_settings()
    probe = settings.model_copy(update={"max_model_requests": 1, "max_search_queries": 0, "max_page_fetches": 0, "max_total_output_tokens": 3000})
    conn = db.connect(copy)
    run_id = conn.execute("SELECT id FROM runs ORDER BY id LIMIT 1").fetchone()[0]
    ro = sqlite3.connect(f"file:{source.as_posix()}?mode=ro", uri=True)
    stored = json.loads(ro.execute("SELECT payload FROM audit_events WHERE event_type = 'draft_input' ORDER BY id LIMIT 1").fetchone()[0])
    ro.close()
    offered = select_draft_passages(conn, run_id, probe)
    ids = {str(k): [p["id"] for p in v] for k, v in offered.items()}
    chars = sum(len(p["text"]) for v in offered.values() for p in v)
    same = ids == stored["passages_offered"]
    print(f"model {probe.drafter_model} | max_tokens {probe.max_output_tokens} | client timeout {probe.mesh_timeout:g}s | job budget: 1 request, 0 searches, 0 fetches, 3000 output tokens")
    print(f"passages offered now: {sum(len(v) for v in ids.values())} ({chars} chars) | system prompt {len(SYSTEM)} chars | same passages as the failed call: {same}")
    print("copy:", copy, "| balance before:", balance())
    if not same:
        sys.exit("the selection differs from the failed call's recorded passages; refusing to send anything")
    if not execute:
        print("dry run: nothing was sent")
        return 0
    svc = build_services(probe, mode="verify")  # real client, models checked against the Mesh catalog (a free GET)
    budget = Budget(probe)
    print("sending exactly one drafting request ...", flush=True)
    started = time.monotonic()
    outcome, claims_ids = "ok", []
    try:
        claims_ids = run_drafter(conn, svc.llm, budget, probe, run_id)
    except BudgetExceeded as exc:  # e.g. an invalid first reply: the second attempt is blocked by the one-request budget
        outcome = f"stopped by the one-request budget after the first attempt: {exc.reason}"
    except (ModelCallError, AgentOutputError) as exc:
        outcome = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    print(f"outcome: {outcome}")
    print(f"measured wall time of the call: {elapsed:.1f} s (client timeout {probe.mesh_timeout:g} s)")
    conn.row_factory = sqlite3.Row
    rows = conn.execute("SELECT id, ok, attempt, latency_ms, prompt_tokens, completion_tokens, total_tokens, finish_reason, error, request_id, structured_output FROM model_calls ORDER BY id").fetchall()
    print("model_calls rows (stored; NULL = the provider returned nothing, not zero):")
    for r in rows:
        print("  ", {k: r[k] for k in ("id", "ok", "attempt", "latency_ms", "prompt_tokens", "completion_tokens", "total_tokens", "finish_reason", "structured_output")}, "| error:", (r["error"] or "")[:160])
    print(f"requests counted against the job budget: {budget.model_requests} of 1")
    print(f"claims drafted: {len(claims_ids)}", f"({sum(len(c['text']) for c in [dict(x) for x in conn.execute('SELECT text FROM claims')])} chars of claim text)" if claims_ids else "")
    print("schema valid:", "yes (the reply parsed against the schema)" if any(r["ok"] for r in rows) else "no valid reply was received")
    for r in rows:
        if r["request_id"]:
            try:
                ev = (get_mesh_client().post("/usage/events", cast_to=object, body={"org_id": "-", "request_id": r["request_id"], "limit": 1}).get("events") or [{}])[0]
                print(f"provider-reported charge for call {r['id']}: cost_usd {ev.get('cost_usd')}")
            except Exception as exc:  # noqa: BLE001
                print(f"provider-reported charge for call {r['id']}: Unavailable ({type(exc).__name__})")
        else:
            print(f"provider-reported charge for call {r['id']}: Unavailable (no request id was returned)")
    print("balance after:", balance())
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
