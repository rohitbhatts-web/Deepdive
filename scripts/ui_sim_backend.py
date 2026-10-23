"""SIMULATED backend for UI testing only. No model, search or fetch call is ever made and no credit is spent.

It serves the REAL FastAPI app and database code, but replaces the research job with a script that writes stage events and
status changes on a timer, so the UI's polling, stage display, failure, stop and terminal handling can be exercised in a browser.
Everything it writes is marked `"simulated": true` in the event payloads. This is not agent acceptance: it says nothing about
research quality or about the real pipeline's timing.

    SIM_DB=<scratch path>.db SIM_STEP_SECONDS=2 uv run python scripts/ui_sim_backend.py

Question text selects the script: contains "FAIL" -> the job crashes; "STOP" -> stops at a budget limit; otherwise completes
(without a report, because no claims exist to put in one).
"""

import os
import sys
import threading
import time
from pathlib import Path

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
STEP = float(os.environ.get("SIM_STEP_SECONDS", "2"))
os.environ.setdefault("DATABASE_PATH", os.environ.get("SIM_DB", "data/ui-sim.db"))

from backend import db
from backend.api import completion, research
from backend.config import get_settings
from backend.main import app


def _event(conn, run_id, kind, stage, **payload):
    with db.tx(conn):
        db.add_event(conn, run_id, kind, stage, {"simulated": True, **payload})


def _model_call(conn, run_id, agent):
    with db.tx(conn):
        db.record_model_call(conn, run_id, agent=agent, model="simulated-model", attempt=1, ok=True,
                             prompt_tokens=100, completion_tokens=20, total_tokens=120)


def simulated_job(run_id: int, svc, verify: bool = False, complete: bool = False) -> None:
    conn = db.connect(get_settings().database_path)
    try:
        question = db.get_run(conn, run_id)["question"]
        script = [
            ("research_started", "graph", {}, None),
            ("queries_generated", "researcher", {"queries": ["simulated query one", "simulated query two"]}, "planner"),
            ("search_completed", "researcher", {"query": "simulated query one"}, "researcher"),
            ("source_ingested", "ingest", {"title": "Simulated source", "final_url": "https://example.test/simulated"}, None),
            ("fetch_failed", "ingest", {"url": "https://example.test/blocked", "outcome": "blocked"}, None),
            ("research_finished", "graph", {"status": "collection_finished"}, None),
            ("claims_drafted", "drafter", {"claim_ids": []}, "drafter"),
            ("coverage_assessed", "coverage", {"round": 0}, "coverage"),
            ("correction_round_started", "correct", {"round": 1}, None),
        ]
        for i, (kind, stage, payload, agent) in enumerate(script):
            time.sleep(STEP)
            if "FAIL" in question and i == 4:
                raise RuntimeError("simulated: search provider returned 500")
            _event(conn, run_id, kind, stage, **payload)
            if agent:
                _model_call(conn, run_id, agent)
            if kind == "research_finished":
                with db.tx(conn):
                    db.set_run_status(conn, run_id, "collection_finished")
        time.sleep(STEP)
        stop = "max_model_requests reached (20)" if "STOP" in question else "all requirements addressed"
        with db.tx(conn):
            db.set_verify_status(conn, run_id, "completed")
            db.set_complete_status(conn, run_id, "stopped" if "STOP" in question else "completed", stop)
            db.add_event(conn, run_id, "completion_finished", "graph", {
                "simulated": True, "status": "stopped" if "STOP" in question else "completed", "stop_reason": stop,
                "report_id": None, "corrective_rounds": 1, "budget_used_of_limit": {"model_requests": [len(script), 20]}})
    except Exception as exc:  # noqa: BLE001 - mirrors graph._run_job: record the failure rather than leave the run active
        with db.tx(conn):
            db.set_run_status(conn, run_id, "failed", f"unexpected {exc}")
            db.set_verify_status(conn, run_id, "failed", f"unexpected {exc}")
            db.set_complete_status(conn, run_id, "failed", f"unexpected {exc}")
            db.add_event(conn, run_id, "research_finished", "graph", {"simulated": True, "status": "failed", "error": str(exc)})
    finally:
        conn.close()


def _no_services():
    return object()  # the simulated job needs none; this replaces the Mesh/search configuration check


app.dependency_overrides[research.get_services] = _no_services
app.dependency_overrides[research.get_verify_services] = _no_services
app.dependency_overrides[completion.get_complete_services] = _no_services
research.execute_research = simulated_job  # the name the endpoint calls

if __name__ == "__main__":
    print("SIMULATED UI TEST BACKEND - no model/search/fetch calls; database:", get_settings().database_path, flush=True)
    threading.current_thread().name = "sim"
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("SIM_PORT", "8000")), log_level="warning")
