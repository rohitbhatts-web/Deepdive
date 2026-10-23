"""Startup recovery of interrupted runs: accurate states and reasons, data preserved, idempotent."""

from backend import db


def _end_to_end_run_cut_off_in_collection(conn):
    """The state the real acceptance run was left in: research, verification and completion all claimed up front, the process died after the first search."""
    rid = db.create_run(conn, "q about tcp and udp")["id"]
    with db.tx(conn):
        assert db.claim_run_for_research(conn, rid)
        assert db.claim_run_for_verification(conn, rid, require_collected=False)
        assert db.claim_run_for_completion(conn, rid, require_collected=False)
        db.record_model_call(conn, rid, agent="planner", model="m", attempt=1, ok=True, prompt_tokens=5, completion_tokens=3, total_tokens=8)
        db.add_event(conn, rid, "research_started", "graph", {})
    return rid


def test_a_run_cut_off_during_collection_records_later_stages_as_not_started(conn):
    rid = _end_to_end_run_cut_off_in_collection(conn)
    assert db.mark_interrupted_runs(conn) == 1
    run = db.get_run(conn, rid)
    assert run["status"] == "failed" and run["stop_reason"] == "interrupted: server restarted during research"
    assert run["verify_status"] == "failed" and run["verify_stop_reason"].startswith("not started") and "during research" in run["verify_stop_reason"]
    assert run["complete_status"] == "failed" and run["complete_stop_reason"].startswith("not started")
    assert "during completion" not in run["complete_stop_reason"] and "during verification" not in run["verify_stop_reason"]
    events = {e["event_type"]: e["payload"] for e in db.list_events(conn, rid) if e["stage"] == "startup"}
    assert events["research_finished"]["stop_reason"] == "interrupted: server restarted during research"
    assert events["verification_finished"]["stop_reason"] == events["completion_finished"]["stop_reason"] == "not started: collection was interrupted"


def test_recovery_keeps_everything_already_stored_and_is_idempotent(conn):
    rid = _end_to_end_run_cut_off_in_collection(conn)
    before = (len(db.list_model_calls(conn, rid)), len(db.list_events(conn, rid)))
    db.mark_interrupted_runs(conn)
    assert len(db.list_model_calls(conn, rid)) == before[0]  # paid calls stay on record
    assert [e["event_type"] for e in db.list_events(conn, rid)][: before[1]] == ["run_created", "research_started"]
    events_after = len(db.list_events(conn, rid))
    assert db.mark_interrupted_runs(conn) == 0  # a second startup changes nothing and adds no events
    assert len(db.list_events(conn, rid)) == events_after


def test_a_completion_cut_off_after_collection_still_says_during_completion(conn):
    rid = db.create_run(conn, "q")["id"]
    with db.tx(conn):
        db.claim_run_for_research(conn, rid)
        db.set_run_status(conn, rid, "collection_finished")
        db.claim_run_for_verification(conn, rid, require_collected=False)
        db.claim_run_for_completion(conn, rid, require_collected=False)
    assert db.mark_interrupted_runs(conn) == 0  # collection was finished
    run = db.get_run(conn, rid)
    assert run["status"] == "collection_finished"  # collected sources stay usable
    assert run["complete_stop_reason"] == "interrupted: server restarted during completion"
    assert run["verify_stop_reason"] == "interrupted: server restarted during verification"
