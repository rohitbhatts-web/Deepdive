import sqlite3

import pytest

from backend import db

SNAP = "First paragraph here.\n\nSecond paragraph here."


def _source(conn, run_id, url="https://example.test/a", snapshot=SNAP, h="h1"):
    return db.insert_source(
        conn, run_id, requested_url=url, final_url=url, title="T", content_type="text/html",
        content_hash=h, snapshot=snapshot, passages=[(0, 21), (23, 45)],
    )


def test_persists_across_connections(tmp_path):
    path = tmp_path / "p.db"
    c1 = db.connect(path)
    run = db.create_run(c1, "What is X?")
    with db.tx(c1):
        sid = _source(c1, run["id"])
    c1.close()

    c2 = db.connect(path)  # re-initialising an existing database must be safe
    assert db.get_run(c2, run["id"])["question"] == "What is X?"
    assert [p["id"] for p in db.list_passages(c2, run["id"])] == [f"S{sid}-P0", f"S{sid}-P1"]
    assert [e["event_type"] for e in db.list_events(c2, run["id"])] == ["run_created"]
    c2.close()


def test_foreign_keys_enforced(conn):
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        _source(conn, run_id=999)
    with pytest.raises(sqlite3.IntegrityError):
        db.add_event(conn, 999, "x", "y")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("INSERT INTO passages VALUES ('p', 999, 0, 't', 0, 1)")
    with pytest.raises(sqlite3.IntegrityError):
        db.record_search(conn, 999, "q", "none")


def test_transaction_rolls_back_on_error(conn):
    run = db.create_run(conn, "Q?")
    with pytest.raises(sqlite3.IntegrityError), db.tx(conn):
        _source(conn, run["id"])
        _source(conn, run["id"])  # same (run, requested_url): violates UNIQUE
    assert db.list_sources(conn, run["id"]) == []
    assert db.list_passages(conn, run["id"]) == []


def test_runs_are_isolated(conn):
    a, b = db.create_run(conn, "A?"), db.create_run(conn, "B?")
    with db.tx(conn):
        _source(conn, a["id"])
        _source(conn, b["id"])  # same URL and hash allowed in a different run
    assert len(db.list_sources(conn, a["id"])) == 1
    assert len(db.list_passages(conn, a["id"])) == 2
    src_b = db.list_sources(conn, b["id"])[0]["id"]
    assert db.get_source(conn, a["id"], src_b) is None
    assert {e["run_id"] for e in db.list_events(conn, a["id"])} == {a["id"]}


def test_passage_text_matches_snapshot(conn):
    run = db.create_run(conn, "Q?")
    with db.tx(conn):
        sid = _source(conn, run["id"])
    snapshot = db.get_source(conn, run["id"], sid)["snapshot"]
    for p in db.list_passages(conn, run["id"]):
        assert snapshot[p["start_offset"]:p["end_offset"]] == p["text"]


def test_subquestion_and_search_tables_usable(conn):
    run = db.create_run(conn, "Q?")
    sq = db.add_subquestion(conn, run["id"], "Sub?", ["a date", "a number"])
    db.record_search(conn, run["id"], "query", "none-yet", subquestion_id=sq, error="not run")
    assert conn.execute("SELECT count(*) FROM searches").fetchone()[0] == 1
