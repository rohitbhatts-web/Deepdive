"""SQLite schema and repository functions. Plain sqlite3; every connection enforces foreign keys."""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from backend.schema_claims import CLAIMS_SCHEMA
from backend.schema_report import REPORT_SCHEMA

SCHEMA_VERSION = 12  # 12: model_call_rejections (bounded, redacted text of rejected model replies, local diagnostics only); 11: cross-claim consistency (consistency_checks/findings, report_consistency/findings); 10: model_call_diagnostics (what the transport observed about each model request); 9: report_answer_refs (a direct answer shown in another subquestion's section); 8: answer relevance (claim_relevance, report_claim_roles, report_relevance); older databases gain the tables on connect

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY,
    question TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    stop_reason TEXT,
    verify_status TEXT NOT NULL DEFAULT 'not_started',
    verify_stop_reason TEXT,
    complete_status TEXT NOT NULL DEFAULT 'not_started',
    complete_stop_reason TEXT
);
CREATE TABLE IF NOT EXISTS subquestions (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    question TEXT NOT NULL,
    evidence_requirements TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS searches (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    subquestion_id INTEGER REFERENCES subquestions(id),
    query TEXT NOT NULL,
    provider TEXT NOT NULL,
    created_at TEXT NOT NULL,
    result_json TEXT,
    error TEXT
);
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    requested_url TEXT NOT NULL,
    final_url TEXT NOT NULL,
    title TEXT,
    retrieved_at TEXT NOT NULL,
    content_type TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    snapshot TEXT NOT NULL,
    UNIQUE (run_id, content_hash),
    UNIQUE (run_id, requested_url)
);
CREATE TABLE IF NOT EXISTS passages (
    id TEXT PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES sources(id),
    ordinal INTEGER NOT NULL,
    text TEXT NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    UNIQUE (source_id, ordinal)
);
CREATE TABLE IF NOT EXISTS fetch_attempts (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    source_id INTEGER REFERENCES sources(id),
    url TEXT NOT NULL,
    outcome TEXT NOT NULL,
    status_code INTEGER,
    reason TEXT,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_events (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    event_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    created_at TEXT NOT NULL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS model_calls (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    latency_ms INTEGER,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    finish_reason TEXT,
    ok INTEGER NOT NULL,
    error TEXT,
    request_id TEXT,
    structured_output INTEGER
);
CREATE TABLE IF NOT EXISTS research_selections (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    search_id INTEGER REFERENCES searches(id),
    url TEXT NOT NULL,
    reason TEXT,
    outcome TEXT NOT NULL,
    source_id INTEGER REFERENCES sources(id),
    fetch_attempt_id INTEGER REFERENCES fetch_attempts(id),
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS source_associations (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL REFERENCES runs(id),
    subquestion_id INTEGER NOT NULL REFERENCES subquestions(id),
    source_id INTEGER NOT NULL REFERENCES sources(id),
    origin TEXT NOT NULL,
    passage_ids TEXT NOT NULL DEFAULT '[]',
    explanation TEXT,
    verification TEXT NOT NULL DEFAULT 'unverified',
    created_at TEXT NOT NULL,
    UNIQUE (subquestion_id, source_id)
);
CREATE INDEX IF NOT EXISTS idx_associations_run ON source_associations(run_id, subquestion_id);
CREATE INDEX IF NOT EXISTS idx_model_calls_run ON model_calls(run_id, id);
-- What was OBSERVED about one model request, beside its model_calls row: elapsed time, the configured deadline, the transport phases httpcore reported, the number
-- of HTTP requests actually written, the status and safe headers, and where it failed (transport, outer deadline, HTTP status, parsing or validation). JSON, redacted:
-- never a request or response body, an Authorization header or a key. Absent for calls recorded before this table existed: that is "not recorded", not "nothing happened".
CREATE TABLE IF NOT EXISTS model_call_diagnostics (
    model_call_id INTEGER PRIMARY KEY REFERENCES model_calls(id),
    diagnostics TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS model_call_diagnostics_no_update BEFORE UPDATE ON model_call_diagnostics
BEGIN SELECT RAISE(ABORT, 'model call diagnostics are immutable'); END;
CREATE TRIGGER IF NOT EXISTS model_call_diagnostics_no_delete BEFORE DELETE ON model_call_diagnostics
BEGIN SELECT RAISE(ABORT, 'model call diagnostics are never deleted'); END;
-- A model reply that was rejected in whole ('envelope') or in part ('items'), kept for LOCAL diagnosis only: never part of a report, an API response or the UI.
-- The content is UNTRUSTED model output stored as inert text: bounded (content_chars is the original length, truncated says whether it was cut), configured
-- secrets redacted, and never any request, header or key. `errors` is the structured validation result WITHOUT input values.
CREATE TABLE IF NOT EXISTS model_call_rejections (
    id INTEGER PRIMARY KEY,
    model_call_id INTEGER NOT NULL REFERENCES model_calls(id),
    stage TEXT NOT NULL CHECK (stage IN ('envelope', 'items')),
    content TEXT NOT NULL,
    content_chars INTEGER NOT NULL,
    truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
    errors TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS model_call_rejections_no_update BEFORE UPDATE ON model_call_rejections
BEGIN SELECT RAISE(ABORT, 'rejected replies are immutable diagnostics'); END;
CREATE TRIGGER IF NOT EXISTS model_call_rejections_no_delete BEFORE DELETE ON model_call_rejections
BEGIN SELECT RAISE(ABORT, 'rejected replies are never deleted'); END;
CREATE INDEX IF NOT EXISTS idx_selections_run ON research_selections(run_id, id);
CREATE INDEX IF NOT EXISTS idx_sources_run ON sources(run_id);
CREATE INDEX IF NOT EXISTS idx_passages_source ON passages(source_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON audit_events(run_id, id);
CREATE INDEX IF NOT EXISTS idx_attempts_run ON fetch_attempts(run_id, id);
"""


def utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(path: str | Path, *, cross_thread: bool = False, read_only: bool = False) -> sqlite3.Connection:
    """Open a connection with foreign keys on and the schema initialised (idempotent). `cross_thread` is for a per-request
    connection that FastAPI sets up, uses and closes in different worker threads (one request at a time, never shared)."""
    path = Path(path)
    if read_only:  # public demo: SQLite itself refuses any write; the schema must already be current (it is created by the seed step)
        conn = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True, isolation_level=None, check_same_thread=not cross_thread)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        if conn.execute("PRAGMA user_version").fetchone()[0] < SCHEMA_VERSION:
            conn.close()
            raise RuntimeError(f"{path} has an older schema than this code ({SCHEMA_VERSION}); read-only mode cannot upgrade it")
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=not cross_thread)  # transactions are explicit, see tx()
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version < SCHEMA_VERSION:
        conn.executescript(SCHEMA)  # creates any missing tables; existing rows are untouched
        for table, column, ddl in (
            ("model_calls", "request_id", "TEXT"),
            ("model_calls", "structured_output", "INTEGER"),
            ("runs", "verify_status", "TEXT NOT NULL DEFAULT 'not_started'"),
            ("runs", "verify_stop_reason", "TEXT"),
            ("runs", "complete_status", "TEXT NOT NULL DEFAULT 'not_started'"),
            ("runs", "complete_stop_reason", "TEXT"),
        ):
            if column not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}")
        conn.executescript(CLAIMS_SCHEMA)
        conn.executescript(REPORT_SCHEMA)
        conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
    return conn


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        # Also reached when COMMIT itself fails (e.g. the database is busy): never leave a transaction open on a connection
        # that a job keeps using, or its next BEGIN would fail with "cannot start a transaction within a transaction".
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row else None


# --- runs -------------------------------------------------------------------------------------


def create_run(conn: sqlite3.Connection, question: str) -> dict:
    now = utcnow()
    with tx(conn):
        cur = conn.execute(
            "INSERT INTO runs (question, status, created_at, updated_at) VALUES (?, 'created', ?, ?)",
            (question, now, now),
        )
        run_id = cur.lastrowid
        add_event(conn, run_id, "run_created", "api", {"question": question})
    return get_run(conn, run_id)


def get_run(conn: sqlite3.Connection, run_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone())


def set_run_status(conn: sqlite3.Connection, run_id: int, status: str, stop_reason: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, stop_reason = ?, updated_at = ? WHERE id = ?",
        (status, stop_reason, utcnow(), run_id),
    )


def claim_run_for_research(conn: sqlite3.Connection, run_id: int) -> bool:
    """Atomically move a run from 'created' to 'researching'. False if it was already started."""
    cur = conn.execute(
        "UPDATE runs SET status = 'researching', updated_at = ? WHERE id = ? AND status = 'created'",
        (utcnow(), run_id),
    )
    return cur.rowcount == 1


def claim_run_for_verification(conn: sqlite3.Connection, run_id: int, *, require_collected: bool) -> bool:
    """Atomically move verify_status 'not_started' -> 'verifying'. With require_collected, the run's collection must
    have ended (finished or stopped) and produced at least one source. False if already started or not eligible."""
    cond = (" AND status IN ('collection_finished', 'stopped')"
            " AND EXISTS (SELECT 1 FROM sources WHERE run_id = runs.id)") if require_collected else ""
    cur = conn.execute(
        f"UPDATE runs SET verify_status = 'verifying', updated_at = ? WHERE id = ? AND verify_status = 'not_started'{cond}",
        (utcnow(), run_id),
    )
    return cur.rowcount == 1


def claim_run_for_completion(conn: sqlite3.Connection, run_id: int, *, require_collected: bool = True) -> bool:
    """Atomically move complete_status 'not_started' -> 'completing'. With require_collected (an existing run), the
    run's collection must have ended with at least one source and its verification must not be running. False if
    already started or not eligible."""
    cond = (" AND status IN ('collection_finished', 'stopped') AND verify_status != 'verifying'"
            " AND EXISTS (SELECT 1 FROM sources WHERE run_id = runs.id)") if require_collected else ""
    cur = conn.execute(
        f"UPDATE runs SET complete_status = 'completing', updated_at = ? WHERE id = ? AND complete_status = 'not_started'{cond}",
        (utcnow(), run_id),
    )
    return cur.rowcount == 1


def claim_run_for_continuation(conn: sqlite3.Connection, run_id: int) -> bool:
    """Atomically move a run whose completion job finished or stopped (verification completed, claims stored) back to 'completing' for an explicit continuation
    that runs under its own budget. The earlier job's reason stays in the audit events. False if not eligible."""
    cur = conn.execute(
        "UPDATE runs SET complete_status = 'completing', complete_stop_reason = NULL, updated_at = ? WHERE id = ? AND status IN ('collection_finished', 'stopped')"
        " AND verify_status = 'completed' AND complete_status IN ('completed', 'stopped') AND EXISTS (SELECT 1 FROM claims WHERE run_id = runs.id)",
        (utcnow(), run_id))
    return cur.rowcount == 1


def claim_run_for_retry(conn: sqlite3.Connection, run_id: int) -> bool:
    """Atomically move a collected run whose verification and completion failed or stopped, with stored evidence and no claim yet, back to
    'verifying' / 'completing' for an explicit continuation. The previous reasons stay in the audit events. False if not eligible."""
    cur = conn.execute(
        "UPDATE runs SET verify_status = 'verifying', verify_stop_reason = NULL, complete_status = 'completing', complete_stop_reason = NULL,"
        " updated_at = ? WHERE id = ? AND status IN ('collection_finished', 'stopped') AND verify_status IN ('failed', 'stopped')"
        " AND complete_status IN ('failed', 'stopped') AND EXISTS (SELECT 1 FROM sources WHERE run_id = runs.id)"
        " AND NOT EXISTS (SELECT 1 FROM claims WHERE run_id = runs.id)", (utcnow(), run_id))
    return cur.rowcount == 1


def set_complete_status(conn: sqlite3.Connection, run_id: int, status: str, stop_reason: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET complete_status = ?, complete_stop_reason = ?, updated_at = ? WHERE id = ?",
        (status, stop_reason, utcnow(), run_id),
    )


def set_verify_status(conn: sqlite3.Connection, run_id: int, status: str, stop_reason: str | None = None) -> None:
    conn.execute(
        "UPDATE runs SET verify_status = ?, verify_stop_reason = ?, updated_at = ? WHERE id = ?",
        (status, stop_reason, utcnow(), run_id),
    )


def mark_interrupted_runs(conn: sqlite3.Connection) -> int:
    """Runs still 'researching' (or verifying / completing) at startup were cut off by a restart; nothing is driving them.

    A run started end to end claims verification and completion up front, but they only begin after collection finishes. A run still 'researching'
    therefore never reached them: those stages are recorded as not started, not as interrupted mid-way, and the stop reasons say so."""
    reason = "interrupted: server restarted during research"
    with tx(conn):
        ids = [r[0] for r in conn.execute("SELECT id FROM runs WHERE status = 'researching'")]
        for run_id in ids:
            conn.execute("UPDATE subquestions SET status = 'collection_incomplete' WHERE run_id = ?"
                         " AND status IN ('open', 'researching')", (run_id,))
            set_run_status(conn, run_id, "failed", reason)
            add_event(conn, run_id, "research_finished", "startup", {"status": "failed", "stop_reason": reason})
        never_reached = "not started: the server restarted during research, before this stage began"
        for run_id in [r[0] for r in conn.execute("SELECT id FROM runs WHERE complete_status = 'completing'")]:
            if run_id in ids:
                set_complete_status(conn, run_id, "failed", never_reached.replace("this stage", "completion"))
                add_event(conn, run_id, "completion_finished", "startup", {"status": "failed", "stop_reason": "not started: collection was interrupted"})
            else:
                set_complete_status(conn, run_id, "failed", "interrupted: server restarted during completion")
                add_event(conn, run_id, "completion_finished", "startup", {"status": "failed", "stop_reason": "interrupted"})
        for run_id in [r[0] for r in conn.execute("SELECT id FROM runs WHERE verify_status = 'verifying'")]:
            if run_id in ids:
                set_verify_status(conn, run_id, "failed", never_reached.replace("this stage", "verification"))
                add_event(conn, run_id, "verification_finished", "startup", {"status": "failed", "stop_reason": "not started: collection was interrupted"})
            else:
                set_verify_status(conn, run_id, "failed", "interrupted: server restarted during verification")
                add_event(conn, run_id, "verification_finished", "startup", {"status": "failed", "stop_reason": "interrupted"})
    return len(ids)


def record_job_interrupted(conn: sqlite3.Connection, run_id: int, signal: str) -> None:
    """Best effort, at the job boundary, when a job is cut short by KeyboardInterrupt / SystemExit / cancellation (not an ordinary error): roll back
    anything half done, mark the run's open stages failed as interrupted and say why. Mirrors `mark_interrupted_runs` (the startup repair for crashes
    where this cannot run) but for one run. Callers re-raise the interrupt afterwards; this never raises an ordinary exception."""
    try:
        if conn.in_transaction:
            conn.rollback()
        run = get_run(conn, run_id)
        if run is None:
            return
        reason = f"interrupted: {signal}"
        with tx(conn):
            researching = run["status"] == "researching"
            if researching:
                conn.execute("UPDATE subquestions SET status = 'collection_incomplete' WHERE run_id = ? AND status IN ('open', 'researching')", (run_id,))
                set_run_status(conn, run_id, "failed", f"{reason} during research")
                add_event(conn, run_id, "research_finished", "graph", {"status": "failed", "stop_reason": reason, "interrupted": True})
            for stage, current, setter, event in (("verification", run["verify_status"], set_verify_status, "verification_finished"),
                                                  ("completion", run["complete_status"], set_complete_status, "completion_finished")):
                if current in ("verifying", "completing"):
                    stop = "not started: collection was interrupted" if researching else reason
                    setter(conn, run_id, "failed", stop if researching else f"{reason} during {stage}")
                    add_event(conn, run_id, event, "graph", {"status": "failed", "stop_reason": stop, "interrupted": True})
    except Exception:  # noqa: BLE001, S110 - cleanup is best effort; the interrupt itself is re-raised by the caller either way
        pass


# --- subquestions / searches -------------------------------------------------------------------


def set_subquestion_status(conn: sqlite3.Connection, subquestion_id: int, status: str) -> None:
    conn.execute("UPDATE subquestions SET status = ? WHERE id = ?", (status, subquestion_id))


def _subquestion(row: sqlite3.Row) -> dict:
    return {**dict(row), "evidence_requirements": json.loads(row["evidence_requirements"])}


def list_subquestions(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM subquestions WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [_subquestion(r) for r in rows]


def get_subquestion(conn: sqlite3.Connection, subquestion_id: int) -> dict:
    return _subquestion(conn.execute("SELECT * FROM subquestions WHERE id = ?", (subquestion_id,)).fetchone())


def add_subquestion(
    conn: sqlite3.Connection, run_id: int, question: str, evidence_requirements: list[str]
) -> int:
    cur = conn.execute(
        "INSERT INTO subquestions (run_id, question, evidence_requirements, status) VALUES (?, ?, ?, 'open')",
        (run_id, question, json.dumps(evidence_requirements)),
    )
    return cur.lastrowid


def record_search(
    conn: sqlite3.Connection,
    run_id: int,
    query: str,
    provider: str,
    subquestion_id: int | None = None,
    result: dict | list | None = None,
    error: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO searches (run_id, subquestion_id, query, provider, created_at, result_json, error)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, subquestion_id, query, provider, utcnow(), json.dumps(result) if result is not None else None, error),
    )
    return cur.lastrowid


# --- sources and passages -----------------------------------------------------------------------


def find_source(conn: sqlite3.Connection, run_id: int, *, requested_url: str | None = None,
                content_hash: str | None = None) -> dict | None:
    if requested_url is not None:
        row = conn.execute(
            "SELECT * FROM sources WHERE run_id = ? AND requested_url = ?", (run_id, requested_url)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT * FROM sources WHERE run_id = ? AND content_hash = ?", (run_id, content_hash)
        ).fetchone()
    return _row(row)


def insert_source(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    requested_url: str,
    final_url: str,
    title: str | None,
    content_type: str,
    content_hash: str,
    snapshot: str,
    passages: list[tuple[int, int]],
) -> int:
    """Insert a source and its passages (given as snapshot spans). Caller supplies the transaction."""
    cur = conn.execute(
        "INSERT INTO sources (run_id, requested_url, final_url, title, retrieved_at, content_type,"
        " content_hash, snapshot) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, requested_url, final_url, title, utcnow(), content_type, content_hash, snapshot),
    )
    source_id = cur.lastrowid
    conn.executemany(
        "INSERT INTO passages (id, source_id, ordinal, text, start_offset, end_offset) VALUES (?, ?, ?, ?, ?, ?)",
        [
            (passage_id(source_id, i), source_id, i, snapshot[start:end], start, end)
            for i, (start, end) in enumerate(passages)
        ],
    )
    return source_id


def passage_id(source_id: int, ordinal: int) -> str:
    """Stable passage ID: derived from the source row and position, never reused or renumbered."""
    return f"S{source_id}-P{ordinal}"


def list_sources(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT s.id, s.run_id, s.requested_url, s.final_url, s.title, s.retrieved_at, s.content_type,"
        " s.content_hash, length(s.snapshot) AS snapshot_chars,"
        " (SELECT count(*) FROM passages p WHERE p.source_id = s.id) AS passage_count"
        " FROM sources s WHERE s.run_id = ? ORDER BY s.id",
        (run_id,),
    ).fetchall()
    return [dict(r) for r in rows]


def get_source(conn: sqlite3.Connection, run_id: int, source_id: int) -> dict | None:
    return _row(conn.execute("SELECT * FROM sources WHERE run_id = ? AND id = ?", (run_id, source_id)).fetchone())


def list_passages(conn: sqlite3.Connection, run_id: int, source_id: int | None = None) -> list[dict]:
    sql = (
        "SELECT p.id, p.source_id, p.ordinal, p.text, p.start_offset, p.end_offset"
        " FROM passages p JOIN sources s ON s.id = p.source_id WHERE s.run_id = ?"
    )
    args: list = [run_id]
    if source_id is not None:
        sql += " AND p.source_id = ?"
        args.append(source_id)
    return [dict(r) for r in conn.execute(sql + " ORDER BY p.source_id, p.ordinal", args).fetchall()]


# --- fetch attempts and audit events --------------------------------------------------------------


def record_fetch_attempt(
    conn: sqlite3.Connection,
    run_id: int,
    url: str,
    outcome: str,
    *,
    status_code: int | None = None,
    reason: str | None = None,
    source_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO fetch_attempts (run_id, source_id, url, outcome, status_code, reason, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, source_id, url, outcome, status_code, reason, utcnow()),
    )
    return cur.lastrowid


def list_fetch_attempts(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute("SELECT * FROM fetch_attempts WHERE run_id = ? ORDER BY id", (run_id,))]


def add_event(conn: sqlite3.Connection, run_id: int, event_type: str, stage: str, payload: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO audit_events (run_id, event_type, stage, created_at, payload) VALUES (?, ?, ?, ?, ?)",
        (run_id, event_type, stage, utcnow(), json.dumps(payload or {})),
    )
    return cur.lastrowid


def list_events(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM audit_events WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [{**dict(r), "payload": json.loads(r["payload"])} for r in rows]


# --- model usage and research selections ---------------------------------------------------------------


def record_model_call(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    agent: str,
    model: str,
    attempt: int,
    ok: bool,
    latency_ms: int | None = None,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
    total_tokens: int | None = None,
    finish_reason: str | None = None,
    error: str | None = None,
    request_id: str | None = None,
    structured_output: bool | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO model_calls (run_id, agent, model, attempt, created_at, latency_ms, prompt_tokens,"
        " completion_tokens, total_tokens, finish_reason, ok, error, request_id, structured_output)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, agent, model, attempt, utcnow(), latency_ms, prompt_tokens, completion_tokens, total_tokens,
         finish_reason, int(ok), error, request_id, None if structured_output is None else int(structured_output)),
    )
    return cur.lastrowid


def record_model_call_diagnostics(conn: sqlite3.Connection, model_call_id: int, diagnostics: dict) -> None:
    conn.execute("INSERT INTO model_call_diagnostics (model_call_id, diagnostics, created_at) VALUES (?, ?, ?)",
                 (model_call_id, json.dumps(diagnostics, sort_keys=True), utcnow()))


REJECTED_REPLY_MAX_CHARS = 16_000  # several times a 1,500-token reply; anything longer is cut and flagged, never stored unbounded


def record_rejected_reply(conn: sqlite3.Connection, model_call_id: int, stage: str, text: str, errors: list[dict], *,
                          max_chars: int = REJECTED_REPLY_MAX_CHARS) -> None:
    """Store a rejected reply for local diagnosis. `text` must already have configured secrets redacted (see LLM.record_rejection)."""
    conn.execute("INSERT INTO model_call_rejections (model_call_id, stage, content, content_chars, truncated, errors, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                 (model_call_id, stage, text[:max_chars], len(text), int(len(text) > max_chars), json.dumps(errors, sort_keys=True, default=str), utcnow()))


def model_call_rejections(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Local diagnostics only (scripts and tests): the stored rejected replies of a run's model calls, oldest first. Not served by the API."""
    rows = conn.execute("SELECT r.* FROM model_call_rejections r JOIN model_calls m ON m.id = r.model_call_id WHERE m.run_id = ? ORDER BY r.id", (run_id,))
    return [{**dict(r), "errors": json.loads(r["errors"]), "truncated": bool(r["truncated"])} for r in rows]


def model_call_diagnostics(conn: sqlite3.Connection, run_id: int) -> dict[int, dict]:
    """model_call id -> its stored diagnostics, for the calls that have any."""
    rows = conn.execute("SELECT d.model_call_id, d.diagnostics FROM model_call_diagnostics d JOIN model_calls m ON m.id = d.model_call_id WHERE m.run_id = ?", (run_id,))
    return {r["model_call_id"]: json.loads(r["diagnostics"]) for r in rows}


def list_model_calls(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM model_calls WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [{**dict(r), "ok": bool(r["ok"]),
             "structured_output": None if r["structured_output"] is None else bool(r["structured_output"])}
            for r in rows]


def model_usage_totals(conn: sqlite3.Connection, run_id: int) -> dict:
    """Sums of the token counts the API reported. Calls that reported none contribute nothing, and a total no call reported is NULL (unknown), never 0:
    a reported zero stays 0. `calls_with_usage` says how many calls reported any count, so a partial total can be told from a complete one."""
    row = conn.execute(
        "SELECT count(*) AS model_calls, coalesce(sum(ok = 0), 0) AS failed_calls,"
        " coalesce(sum(prompt_tokens IS NOT NULL OR completion_tokens IS NOT NULL OR total_tokens IS NOT NULL), 0) AS calls_with_usage,"
        " sum(prompt_tokens) AS prompt_tokens, sum(completion_tokens) AS completion_tokens,"
        " sum(total_tokens) AS total_tokens FROM model_calls WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    return dict(row)


def add_selection(
    conn: sqlite3.Connection,
    run_id: int,
    subquestion_id: int,
    url: str,
    outcome: str,
    *,
    search_id: int | None = None,
    reason: str | None = None,
    source_id: int | None = None,
    fetch_attempt_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO research_selections (run_id, subquestion_id, search_id, url, reason, outcome, source_id,"
        " fetch_attempt_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, subquestion_id, search_id, url, reason, outcome, source_id, fetch_attempt_id, utcnow()),
    )
    return cur.lastrowid


def list_selections(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM research_selections WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [dict(r) for r in rows]


def list_searches(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """Search history; each search carries the URLs selected from it and what became of them."""
    selections = list_selections(conn, run_id)
    out = []
    for r in conn.execute("SELECT * FROM searches WHERE run_id = ? ORDER BY id", (run_id,)).fetchall():
        d = dict(r)
        raw = d.pop("result_json")
        d["result"] = json.loads(raw) if raw else None
        d["selections"] = [s for s in selections if s["search_id"] == d["id"]]
        out.append(d)
    return out


def research_counts(conn: sqlite3.Connection, run_id: int) -> dict:
    def one(sql: str) -> int:
        return conn.execute(sql, (run_id,)).fetchone()[0]

    fetched = "FROM fetch_attempts WHERE run_id = ? AND outcome NOT LIKE 'duplicate%'"
    return {
        "searches": one("SELECT count(*) FROM searches WHERE run_id = ?"),
        "failed_searches": one("SELECT count(*) FROM searches WHERE run_id = ? AND error IS NOT NULL"),
        "sources": one("SELECT count(*) FROM sources WHERE run_id = ?"),
        "passages": one("SELECT count(*) FROM passages p JOIN sources s ON s.id = p.source_id WHERE s.run_id = ?"),
        "associations": one("SELECT count(*) FROM source_associations WHERE run_id = ?"),
        "associations_from_relevance_assessment": one(
            "SELECT count(*) FROM source_associations WHERE run_id = ? AND origin = 'relevance_assessment'"),
        "page_fetch_attempts": one(f"SELECT count(*) {fetched}"),
        "failed_fetches": one(f"SELECT count(*) {fetched} AND outcome != 'ok'"),
    }


# --- source <-> subquestion associations (always 'unverified' until a verifier says otherwise) -----------------


def add_association(
    conn: sqlite3.Connection,
    run_id: int,
    subquestion_id: int,
    source_id: int,
    origin: str,
    *,
    passage_ids: list[str] | None = None,
    explanation: str | None = None,
) -> bool:
    """Link a source to a subquestion. Returns False if that pair was already linked."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO source_associations (run_id, subquestion_id, source_id, origin, passage_ids,"
        " explanation, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (run_id, subquestion_id, source_id, origin, json.dumps(passage_ids or []), explanation, utcnow()),
    )
    return cur.rowcount == 1


def list_associations(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    rows = conn.execute("SELECT * FROM source_associations WHERE run_id = ? ORDER BY id", (run_id,)).fetchall()
    return [{**dict(r), "passage_ids": json.loads(r["passage_ids"])} for r in rows]


def source_passages(conn: sqlite3.Connection, source_id: int) -> list[dict]:
    rows = conn.execute("SELECT id, source_id, ordinal, text FROM passages WHERE source_id = ? ORDER BY ordinal", (source_id,))
    return [dict(r) for r in rows]
