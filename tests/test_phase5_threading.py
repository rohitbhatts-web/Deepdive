"""Concurrency and cleanup of database connections: one connection per request or job, explicit transactions, reliable rollback."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from backend import db


class FailingCommit:
    """FIXTURE: forwards to a real connection but makes the first COMMIT fail, like a busy database."""

    def __init__(self, conn):
        self.conn, self.armed = conn, True

    @property
    def in_transaction(self):
        return self.conn.in_transaction

    def execute(self, sql, *args):
        if sql == "COMMIT" and self.armed:
            self.armed = False
            raise sqlite3.OperationalError("database is locked")
        return self.conn.execute(sql, *args)


def test_failed_commit_rolls_back_and_the_connection_stays_usable(tmp_path):
    conn = db.connect(tmp_path / "t.db")
    wrapped = FailingCommit(conn)
    with pytest.raises(sqlite3.OperationalError), db.tx(wrapped):
        conn.execute("INSERT INTO runs (question, status, created_at, updated_at) VALUES ('lost', 'created', 'x', 'x')")
    assert not conn.in_transaction
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 0  # nothing from the failed transaction survived
    with db.tx(conn):  # a long-lived job connection can start its next transaction
        conn.execute("INSERT INTO runs (question, status, created_at, updated_at) VALUES ('kept', 'created', 'x', 'x')")
    assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] == 1


def test_exception_inside_a_transaction_rolls_back_and_releases_the_write_lock(tmp_path):
    path = tmp_path / "t.db"
    a, b = db.connect(path), db.connect(path)
    with pytest.raises(RuntimeError), db.tx(a):
        a.execute("INSERT INTO runs (question, status, created_at, updated_at) VALUES ('x', 'created', 'x', 'x')")
        raise RuntimeError("boom")
    assert not a.in_transaction
    b.execute("PRAGMA busy_timeout = 100")
    assert db.create_run(b, "another connection can write at once")["id"] == 1  # the lock was released, the failed insert undone


def test_concurrent_writers_and_readers_on_separate_connections(tmp_path):
    path = tmp_path / "t.db"
    db.connect(path).close()
    errors: list[BaseException] = []

    def writer(n):
        conn = db.connect(path)  # one connection per thread, as a job or request has
        try:
            for i in range(10):
                db.create_run(conn, f"question {n}-{i}")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    def reader():
        conn = db.connect(path)
        try:
            for _ in range(30):
                assert conn.execute("SELECT count(*) FROM runs").fetchone()[0] >= 0
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            conn.close()

    with ThreadPoolExecutor(8) as pool:
        for f in [pool.submit(writer, n) for n in range(6)] + [pool.submit(reader) for _ in range(2)]:
            f.result()
    assert errors == []
    check = db.connect(path)
    assert check.execute("SELECT count(*) FROM runs").fetchone()[0] == 60
    assert check.execute("SELECT count(DISTINCT question) FROM runs").fetchone()[0] == 60
    assert check.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_each_request_gets_its_own_connection_and_it_is_closed_afterwards(real_get_db):
    first, second = real_get_db(), real_get_db()
    c1, c2 = next(first), next(second)
    assert c1 is not c2
    first.close()  # generator cleanup (the `finally`) closes the connection
    with pytest.raises(sqlite3.ProgrammingError):
        c1.execute("SELECT 1")
    assert c2.execute("SELECT 1").fetchone()[0] == 1
    with pytest.raises(RuntimeError):  # an endpoint error is thrown into the generator: still closed
        second.throw(RuntimeError("endpoint failed"))
    with pytest.raises(sqlite3.ProgrammingError):
        c2.execute("SELECT 1")


def test_request_connection_does_not_leak_an_open_transaction_to_the_next_request(client, real_get_db):
    gen = real_get_db()
    conn = next(gen)
    conn.execute("BEGIN IMMEDIATE")  # an endpoint that died between BEGIN and COMMIT
    gen.close()
    other = db.connect(client.db_path)
    other.execute("PRAGMA busy_timeout = 100")
    assert db.create_run(other, "writable after the abandoned request")["id"] >= 1


def test_job_connections_are_separate_and_enforce_their_thread(tmp_path):
    """Jobs open their own connection (services.open_db -> db.connect) with the thread check ON: sharing one across threads fails loudly."""
    path = tmp_path / "t.db"
    a, b = db.connect(path), db.connect(path)
    assert a is not b
    seen: list[BaseException] = []

    def use():
        try:
            a.execute("SELECT 1")
        except BaseException as exc:  # noqa: BLE001
            seen.append(exc)

    t = threading.Thread(target=use)
    t.start()
    t.join()
    assert len(seen) == 1 and isinstance(seen[0], sqlite3.ProgrammingError)


def test_api_reads_and_writes_during_a_long_write_transaction(client):
    """A job holding the write lock must not block API reads (WAL); an API write waits, then succeeds once the job commits."""
    job = db.connect(client.db_path, cross_thread=True)  # test only: the timer thread commits it
    rid = db.create_run(job, "being worked on")["id"]
    job.execute("BEGIN IMMEDIATE")
    job.execute("UPDATE runs SET updated_at = 'later' WHERE id = ?", (rid,))
    assert client.get(f"/runs/{rid}").status_code == 200  # reader is not blocked
    timer = threading.Timer(0.3, lambda: job.execute("COMMIT"))
    timer.start()
    resp = client.post("/runs", json={"question": "created while the job holds the write lock"})
    timer.join()
    assert resp.status_code == 201
