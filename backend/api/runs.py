"""Run, source, passage and event endpoints. URL ingestion here is explicit, not autonomous research."""

import sqlite3
from collections.abc import Iterator
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import BaseModel, Field, StringConstraints

from backend import db
from backend.config import get_settings
from backend.ingest import ingest_url
from backend.web import Fetcher

router = APIRouter()

SQLITE_MAX_INT = 2**63 - 1  # larger IDs would overflow SQLite and surface as a 500
RunId = Annotated[int, Path(ge=1, le=SQLITE_MAX_INT)]
Stripped = StringConstraints(strip_whitespace=True)


def get_db() -> Iterator[sqlite3.Connection]:
    conn = db.connect(get_settings().database_path, cross_thread=True)
    try:
        yield conn
    finally:
        conn.close()


def get_fetcher() -> Fetcher:
    return Fetcher()


class RunCreate(BaseModel):
    question: Annotated[str, Stripped, Field(min_length=3, max_length=2000)]


class RunOut(BaseModel):
    id: int
    question: str
    status: str
    created_at: str
    updated_at: str
    stop_reason: str | None


class RunSummary(RunOut):
    verify_status: str
    complete_status: str
    report_outcome: str | None  # outcome of the newest report, if one exists


class RunPage(BaseModel):
    items: list[RunSummary]
    total: int
    limit: int
    offset: int


class LimitsOut(BaseModel):
    """The configured per-run bounds (the same Budget limits every run is charged against) and the question length bounds."""
    limits: dict[str, Any]
    question_min_chars: int
    question_max_chars: int
    max_correction_rounds: int


class SourceCreate(BaseModel):
    url: Annotated[str, Stripped, Field(min_length=1, max_length=2048)]


class SourceOut(BaseModel):
    id: int
    run_id: int
    requested_url: str
    final_url: str
    title: str | None
    retrieved_at: str
    content_type: str
    content_hash: str
    snapshot_chars: int
    passage_count: int


class IngestOut(BaseModel):
    status: str
    outcome: str
    source: SourceOut


class PassageOut(BaseModel):
    id: str
    source_id: int
    ordinal: int
    text: str
    start_offset: int
    end_offset: int


class EventOut(BaseModel):
    id: int
    run_id: int
    event_type: str
    stage: str
    created_at: str
    payload: dict[str, Any]


# fetch outcome -> HTTP status for failed ingestion
FAILURE_STATUS = {
    "blocked": 422,
    "no_extractable_text": 422,
    "unsupported_content_type": 415,
    "too_large": 413,
    "timeout": 504,
}


def _require_run(conn: sqlite3.Connection, run_id: int) -> dict:
    run = db.get_run(conn, run_id)
    if run is None:
        raise HTTPException(404, f"run {run_id} not found")
    return run


def _source_out(conn: sqlite3.Connection, run_id: int, source_id: int) -> dict:
    return next(s for s in db.list_sources(conn, run_id) if s["id"] == source_id)


@router.get("/runs", response_model=RunPage)
def list_runs(limit: Annotated[int, Query(ge=1, le=100)] = 30, offset: Annotated[int, Query(ge=0, le=SQLITE_MAX_INT)] = 0,
              conn: sqlite3.Connection = Depends(get_db)):
    """Newest runs first. Read-only; used by the UI's recent-runs list."""
    total = conn.execute("SELECT count(*) FROM runs").fetchone()[0]
    rows = conn.execute(
        "SELECT r.*, (SELECT outcome FROM reports WHERE run_id = r.id ORDER BY id DESC LIMIT 1) AS report_outcome"
        " FROM runs r ORDER BY r.id DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
    return RunPage(items=[dict(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/limits", response_model=LimitsOut)
def get_limits():
    from backend.budget import Budget

    s = get_settings()
    return LimitsOut(limits={k: v[1] for k, v in Budget(s).snapshot().items()}, question_min_chars=3, question_max_chars=2000,
                     max_correction_rounds=s.max_correction_rounds)


def _summary(conn: sqlite3.Connection, run: dict) -> dict:
    row = conn.execute("SELECT outcome FROM reports WHERE run_id = ? ORDER BY id DESC LIMIT 1", (run["id"],)).fetchone()
    return {**run, "report_outcome": row["outcome"] if row else None}


@router.post("/runs", response_model=RunSummary, status_code=201)
def create_run(body: RunCreate, conn: sqlite3.Connection = Depends(get_db)):
    return _summary(conn, db.create_run(conn, body.question))


@router.get("/runs/{run_id}", response_model=RunSummary)
def get_run(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    return _summary(conn, _require_run(conn, run_id))


@router.post("/runs/{run_id}/sources", response_model=IngestOut, status_code=201)
def add_source(
    run_id: RunId,
    body: SourceCreate,
    response: Response,
    conn: sqlite3.Connection = Depends(get_db),
    fetcher: Fetcher = Depends(get_fetcher),
):
    _require_run(conn, run_id)
    result = ingest_url(conn, fetcher, run_id, body.url)
    if result.status == "failed":
        raise HTTPException(
            FAILURE_STATUS.get(result.outcome, 502),
            {"outcome": result.outcome, "reason": result.reason, "fetch_attempt_id": result.fetch_attempt_id},
        )
    if result.status == "duplicate":
        response.status_code = 200
    return IngestOut(
        status=result.status, outcome=result.outcome, source=_source_out(conn, run_id, result.source_id)
    )


@router.get("/runs/{run_id}/sources", response_model=list[SourceOut])
def list_sources(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    return db.list_sources(conn, run_id)


@router.get("/runs/{run_id}/passages", response_model=list[PassageOut])
def list_passages(
    run_id: RunId,
    source_id: Annotated[int | None, Query(ge=1, le=SQLITE_MAX_INT)] = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    _require_run(conn, run_id)
    return db.list_passages(conn, run_id, source_id)


@router.get("/runs/{run_id}/events", response_model=list[EventOut])
def list_events(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    return db.list_events(conn, run_id)
