"""The run record: complete, read-only, failures kept, unknown usage never shown as zero. Fixture databases only; no network."""

import hashlib
import sys
from pathlib import Path

import pytest

from backend import db
from backend.agents import planner
from backend.agents.schemas import Plan
from tests.test_answer_completeness import coverage_world
from tests.test_phase4_units import make_report

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import run_record as rr

Q = "How do Git merge and Git rebase differ, and when should a developer use each?"
BAD = Plan.model_validate({"asks": [{"quote": "How do Git merge and Git rebase differ", "label": "difference"}], "subquestions": [
    {"question": "What differs between merge and rebase", "evidence_requirements": ["a fact"], "rationale": "why", "covers": [0]}]})


@pytest.fixture
def failed_db(tmp_path):
    path = tmp_path / "failed.db"
    c = db.connect(path)
    run = db.create_run(c, Q)
    db.add_event(c, run["id"], "plan_rejected", "planner", planner.rejection_payload(BAD, ["no ask covers this part of the question: 'when should a developer use each?'"], 1))
    db.record_model_call(c, run["id"], agent="planner", model="m1", attempt=1, ok=True, latency_ms=55610, prompt_tokens=667, completion_tokens=379, total_tokens=1046, finish_reason="stop", request_id="req_A")
    db.record_model_call(c, run["id"], agent="researcher", model="m1", attempt=1, ok=False, latency_ms=2046, error="InternalServerError: 503 upstream_error", request_id="req_B")
    db.add_event(c, run["id"], "research_finished", "graph", {"status": "failed", "stop_reason": "researcher failed", "budget_used_of_limit": {
        "model_requests": [3, 20], "search_queries": [0, 8], "page_fetches": [0, 12], "candidate_claims": [0, 4], "output_tokens": [2126, 15000], "elapsed_seconds": [68.6, 300.0]}})
    with db.tx(c):
        db.set_run_status(c, run["id"], "failed", "researcher failed on subquestion 1")
    c.close()
    return path, run["id"]


def record(path, run_id):
    conn = rr.open_readonly(path)
    try:
        return rr.build_record(conn, run_id, str(path))
    finally:
        conn.close()


def test_a_failed_run_keeps_its_failures_the_rejected_plan_and_unknown_usage(failed_db):
    text = record(*failed_db)
    assert "collection: **failed** — researcher failed on subquestion 1" in text
    assert "Rejected plan (event" in text and "- difference: “How do Git merge and Git rebase differ”" in text and "covers asks [0]" in text
    assert "| 2 | researcher | m1 | 1 | False | 2046 | req_B | unknown | unknown | unknown | unknown | InternalServerError: 503 upstream_error |" in text  # unknown, not 0
    assert "usage reported by 1 of 2" in text and "prompt tokens: 667; completion tokens: 379" in text and "a lower bound" in text
    assert "3 / 20" in text and "2126 / 15000" in text and "reservation against the cap" in text
    assert "No report was stored for this run." in text and "_none recorded_" in text  # no searches, sources or claims, said plainly


def test_a_run_with_no_usage_at_all_never_prints_zero_tokens(tmp_path):
    c = db.connect(tmp_path / "x.db")
    run = db.create_run(c, Q)
    db.record_model_call(c, run["id"], agent="planner", model="m", attempt=1, ok=False, latency_ms=60031, error="TimeoutError")
    c.close()
    text = record(tmp_path / "x.db", run["id"])
    assert "usage reported by 0 of 1" in text and "prompt tokens: unknown; completion tokens: unknown; total tokens: unknown" in text


def test_claims_report_and_unresolved_requirements_are_recorded(tmp_path, settings):
    c = db.connect(tmp_path / "w.db")
    world, _ids = coverage_world(c, settings)
    make_report(c, world, None)
    c.close()
    text = record(tmp_path / "w.db", world["run"])
    assert "## Claims" in text and "eligible for report" in text and "### Unresolved requirements" in text
    assert "outcome: **" in text and "No report was stored" not in text


def test_the_database_is_opened_read_only_and_the_output_is_never_overwritten(failed_db, tmp_path, capsys):
    path, run_id = failed_db
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    out = tmp_path / "rec.md"
    assert rr.main([str(path), "--run", str(run_id), "--out", str(out)]) == 0 and out.exists()
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before  # the database file is byte-identical
    first = out.read_text(encoding="utf-8")
    assert rr.main([str(path), "--run", str(run_id), "--out", str(out)]) == 2 and out.read_text(encoding="utf-8") == first
    assert "Refusing to overwrite" in capsys.readouterr().out
    conn = rr.open_readonly(path)
    with pytest.raises(Exception, match="readonly"):
        conn.execute("DELETE FROM runs")
    conn.close()
    assert rr.main([str(tmp_path / "missing.db"), "--run", "1", "--out", str(tmp_path / "n.md")]) == 2


def test_transport_diagnostics_are_listed_and_a_call_without_them_says_not_recorded(failed_db):
    path, run_id = failed_db
    c = db.connect(path)
    second = c.execute("SELECT id FROM model_calls WHERE agent = 'researcher'").fetchone()["id"]
    db.record_model_call_diagnostics(c, second, {"outcome": "failed", "failure_stage": "http_status", "elapsed_ms": 2046, "deadline_s": 60.0,
        "transport": {"http_requests_sent": 1, "connect_attempts": 1, "status_code": 503, "response_headers": {"retry-after": "7"}}, "error": {"error_class": "upstream_server_error"}})
    c.close()
    text = record(path, run_id)
    assert "| 1 | not recorded |" in text  # the planner call predates diagnostics here
    assert f"| {second} | failed | http_status | 2046 | 60.0 |" in text and "upstream_server_error" in text and '{"retry-after": "7"}' in text


def test_mesh_routing_columns_say_not_recorded_unknown_or_the_validated_value(failed_db):
    path, run_id = failed_db
    c = db.connect(path)
    first, second = [r["id"] for r in c.execute("SELECT id FROM model_calls ORDER BY id")]
    base = {"outcome": "ok", "failure_stage": None, "elapsed_ms": 5, "deadline_s": 90.0, "error": {}}
    mesh = {"routing_attempts": 3, "routing_fallback": True, "provider_latency_ms": 46012, "byok_fallback_triggered": None, "present": [], "unrecognized": []}
    db.record_model_call_diagnostics(c, first, {**base, "transport": {"http_requests_sent": 1, "connect_attempts": 1, "status_code": 200, "response_headers": {}, "mesh": mesh}})
    db.record_model_call_diagnostics(c, second, {**base, "transport": {"http_requests_sent": 1, "connect_attempts": 1, "status_code": 200, "response_headers": {}}})  # recorded before the Mesh columns
    c.close()
    text = record(path, run_id)
    assert "| gateway attempts | routing fallback | provider latency ms | BYOK fallback |" in text
    assert "| 3 | True | 46012 | unknown |" in text  # a validated value, and an unknown one that is not shown as false or 0
    assert "| not recorded | not recorded | not recorded | not recorded |" in text
