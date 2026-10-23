"""Wall-clock deadlines that cancel the HTTP operation, and interruption handling at the job boundary.

A deterministic localhost server stands in for the provider (no credentials, no real network). It shows what the REAL async transport does: a silent socket and a
slowly trickling reply both stop at the deadline, the connection is closed (the server sees it), nothing keeps running or retries, and the failed call is recorded
with its measured wait while usage stays unknown. It does NOT show that a provider stopped generating or billing when the client disconnected: that is unknowable
from the client, so usage and charges stay NULL/unknown."""

import _thread
import json
import socket
import sqlite3
import threading
import time

import pytest
from openai import AsyncOpenAI
from pydantic import BaseModel

from backend import db
from backend.budget import Budget
from backend.llm import LLM, ModelCallError
from evaluation import e2e_eval


class Answer(BaseModel):
    ok: str


BODY = json.dumps({"id": "x", "object": "chat.completion", "created": 0, "model": "m",
                   "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": '{"ok": "yes"}'}}],
                   "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10}}).encode()


class Server:
    """Accepts connections in a thread. mode: silent (never replies), drip (valid reply, one byte per `gap` seconds), fast (full reply at once)."""

    def __init__(self, mode, gap=0.25):
        self.mode, self.gap = mode, gap
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.accepted = 0
        self.client_closed = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self):
        while not self.stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            self.accepted += 1
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        conn.settimeout(0.1)
        try:
            data = b""
            while b"\r\n\r\n" not in data:  # the request headers (the body may follow; it is not needed)
                data += conn.recv(65536)
            if self.mode == "fast":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(BODY) + BODY)
                return
            if self.mode == "drip":
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: %d\r\n\r\n" % len(BODY))
            deadline = time.monotonic() + 20
            sent = 0
            while time.monotonic() < deadline and not self.stop.is_set():
                if self.mode == "drip" and sent < len(BODY):
                    conn.sendall(BODY[sent:sent + 1])
                    sent += 1
                    time.sleep(self.gap)
                    continue
                try:
                    if conn.recv(1) == b"":  # the client closed its end
                        self.client_closed.set()
                        return
                except TimeoutError:
                    pass
        except OSError:  # a send/recv on a connection the client has closed
            self.client_closed.set()
        finally:
            conn.close()

    def close(self):
        self.stop.set()
        self.sock.close()


@pytest.fixture
def make_llm(conn, settings):
    servers = []

    def build(mode, **overrides):
        s = settings.model_copy(update=overrides)
        srv = Server(mode)
        servers.append(srv)
        factory = lambda: AsyncOpenAI(api_key="dummy", base_url=f"http://127.0.0.1:{srv.port}/v1", max_retries=0, timeout=s.mesh_timeout)
        return LLM(None, s, frozenset(), async_client_factory=factory), Budget(s), srv, db.create_run(conn, "question")["id"], s

    yield build
    for srv in servers:
        srv.close()


def call(llm, conn, budget, run_id):
    return llm.call(conn, run_id, budget, agent="verifier", model="m", system="s", user="u", schema=Answer)


def rows(conn):
    return conn.execute("SELECT ok, attempt, latency_ms, prompt_tokens, completion_tokens, total_tokens, finish_reason, error FROM model_calls ORDER BY id").fetchall()


@pytest.mark.parametrize("mode", ["silent", "drip"])
def test_a_silent_or_trickling_reply_stops_at_the_deadline_and_the_connection_is_closed(conn, make_llm, mode):
    threads_before = threading.active_count()
    llm, budget, srv, run_id, s = make_llm(mode, mesh_timeout=1.0)
    started = time.monotonic()
    with pytest.raises(ModelCallError, match="wall-clock deadline of 1s reached"):
        call(llm, conn, budget, run_id)
    elapsed = time.monotonic() - started
    # the trickle alone would take len(BODY) * 0.25 s (about a minute); the deadline cut it off at about 1 s
    assert 0.9 <= elapsed < 3.0, elapsed
    assert srv.client_closed.wait(3), "the server never saw the connection close: the request was not cancelled at the transport"
    (row,) = rows(conn)
    assert row["ok"] == 0 and 900 <= row["latency_ms"] < 3000  # the measured wait is recorded
    assert row["prompt_tokens"] is None and row["completion_tokens"] is None and row["finish_reason"] is None  # usage stays unknown
    assert budget.model_requests == 1 and budget.output_tokens == s.max_output_tokens  # counted once; the per-call maximum is assumed
    time.sleep(1.5)  # nothing abandoned keeps running: no later write, no hidden retry, no extra connection
    assert len(rows(conn)) == 1 and srv.accepted == 1
    assert threading.active_count() <= threads_before + 2  # the server's own threads only; no leaked request threads


def test_the_remaining_job_time_bounds_the_call_even_when_the_mesh_timeout_is_longer(conn, make_llm):
    llm, budget, srv, run_id, _ = make_llm("drip", mesh_timeout=60.0, max_elapsed_seconds=1.5, min_call_seconds=0.5)
    started = time.monotonic()
    with pytest.raises(ModelCallError, match="wall-clock deadline"):
        call(llm, conn, budget, run_id)
    assert time.monotonic() - started < 4.0 and srv.client_closed.wait(3)


def test_a_normal_reply_through_the_async_transport_records_the_reported_usage(conn, make_llm):
    llm, budget, srv, run_id, _ = make_llm("fast", mesh_timeout=5.0)
    assert call(llm, conn, budget, run_id).ok == "yes"
    (row,) = rows(conn)
    assert row["ok"] == 1 and (row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (7, 3, 10) and row["finish_reason"] == "stop"
    assert budget.model_requests == 1 and srv.accepted == 1


def test_an_interrupt_during_a_request_cancels_it_and_closes_the_connection(conn, make_llm):
    llm, budget, srv, run_id, _ = make_llm("silent", mesh_timeout=30.0)
    timer = threading.Timer(0.5, _thread.interrupt_main)
    started = time.monotonic()
    timer.start()
    try:
        with pytest.raises(KeyboardInterrupt):
            call(llm, conn, budget, run_id)
    finally:
        timer.cancel()
    assert time.monotonic() - started < 5.0 and srv.client_closed.wait(3)
    assert rows(conn) == []  # an interrupt is not a model failure: nothing is invented for it


# --- job boundary -----------------------------------------------------------------------------------------------------------------------

class InterruptingLLM:
    structured_models = frozenset()

    def __init__(self, exc):
        self.exc = exc

    def call(self, *a, **k):
        raise self.exc


@pytest.mark.parametrize("exc", [KeyboardInterrupt(), SystemExit(3)])
def test_a_job_interrupt_is_recorded_best_effort_and_then_re_raised(tmp_path, settings, fetcher, exc):
    from backend.graph import execute_research
    from backend.services import ResearchServices

    path = tmp_path / "job.db"
    c = db.connect(path)
    run_id = db.create_run(c, "question")["id"]
    with db.tx(c):
        db.claim_run_for_research(c, run_id)
        db.claim_run_for_verification(c, run_id, require_collected=False)
        db.claim_run_for_completion(c, run_id, require_collected=False)
    c.close()
    svc = ResearchServices(settings, InterruptingLLM(exc), None, fetcher, lambda: db.connect(path))
    with pytest.raises(type(exc)):  # never swallowed, never turned into success
        execute_research(run_id, svc, verify=True, complete=True)
    c = db.connect(path)
    run = db.get_run(c, run_id)
    assert run["status"] == "failed" and run["stop_reason"].startswith(f"interrupted: {type(exc).__name__}")
    assert run["verify_status"] == "failed" and run["complete_status"] == "failed"
    ev = [json.loads(p) for (p,) in c.execute("SELECT payload FROM audit_events WHERE event_type = 'research_finished'")]
    assert ev and ev[-1]["interrupted"] is True and not c.in_transaction
    c.close()


# --- evaluation observability and incremental persistence -------------------------------------------------------------------------------

def test_progress_prints_concise_stage_lines_from_a_read_only_view(tmp_path):
    path = tmp_path / "p.db"
    c = db.connect(path)
    run_id = db.create_run(c, "question")["id"]
    db.add_event(c, run_id, "plan_created", "planner", {})
    db.add_event(c, run_id, "source_ingested", "ingest", {})
    c.commit()
    lines = []
    prog = e2e_eval.Progress(path, run_id, "Q02@1", say=lines.append)
    prog.poll(time.monotonic())
    assert len(lines) == 1 and lines[0].startswith("[Q02@1]") and "plan_created" in lines[0] and "source_ingested" not in lines[0] and "model calls 0" in lines[0]
    db.add_event(c, run_id, "source_ingested", "ingest", {})
    c.commit()
    prog.poll(time.monotonic())
    assert len(lines) == 1  # a non-stage event alone prints nothing before the heartbeat
    c.close()


def test_an_interruption_keeps_the_questions_already_finished(tmp_path, monkeypatch, settings):
    from backend import graph, services

    path, out, lines, ran = tmp_path / "e.db", tmp_path / "e.json", [], []
    s = settings.model_copy(update={"database_path": str(path)})
    monkeypatch.setattr(services, "build_services", lambda st, mode="complete": services.ResearchServices(st, None, None, None, lambda: db.connect(path)))

    def fake_job(run_id, svc, verify=False, complete=False):
        ran.append(run_id)
        if len(ran) == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(graph, "execute_research", fake_job)
    with pytest.raises(KeyboardInterrupt):
        e2e_eval.execute("dev", s, path, out, confirm=True, question_ids=["Q02", "Q05"], say=lines.append)
    partial = json.loads(out.with_suffix(".partial.json").read_text(encoding="utf-8"))
    assert partial["status"] == "interrupted" and [q["question_id"] for q in partial["completed_questions"]] == ["Q02"]
    assert partial["interrupted_question"]["question_id"] == "Q05" and not out.exists()  # no final result is invented
    assert any("question 1/2 started" in x for x in lines) and any("[Q02@1] finished" in x for x in lines) and any("INTERRUPTED" in x for x in lines)
    assert sqlite3.connect(path).execute("select count(*) from runs").fetchone()[0] == 2
