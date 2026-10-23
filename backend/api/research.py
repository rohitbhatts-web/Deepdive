"""Start research on a run and inspect its status, subquestions, searches and model usage."""

import sqlite3
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from backend import db
from backend.api.runs import RunId, _require_run, get_db
from backend.budget import Budget
from backend.config import get_settings
from backend.graph import execute_research, execute_verification
from backend.services import ConfigurationError, ResearchServices, build_services

router = APIRouter()


def _services(mode: str) -> ResearchServices:
    try:
        return build_services(mode=mode)
    except ConfigurationError as exc:
        raise HTTPException(503, {"error": "not_configured", "message": str(exc), "missing": exc.missing}) from exc


def get_services(verify: bool = False, complete: bool = False) -> ResearchServices:
    """Collection needs search + planner/researcher models; ?verify=true or ?complete=true also drafter/verifier models."""
    return _services("all" if verify or complete else "collect")


def get_verify_services() -> ResearchServices:
    """Verification of an already collected run needs only Mesh and the drafter/verifier models: no search key."""
    return _services("verify")


class ResearchStarted(BaseModel):
    run_id: int
    status: str


class ResearchStatus(BaseModel):
    run_id: int
    status: str
    stop_reason: str | None
    updated_at: str
    subquestions_by_status: dict[str, int]
    counts: dict[str, int]
    model_usage: dict[str, int]
    limits: dict[str, Any]


class SubQuestionOut(BaseModel):
    id: int
    run_id: int
    question: str
    evidence_requirements: list[str]
    status: str


class SelectionOut(BaseModel):
    id: int
    subquestion_id: int
    url: str
    reason: str | None
    outcome: str
    source_id: int | None
    fetch_attempt_id: int | None


class SearchOut(BaseModel):
    id: int
    subquestion_id: int | None
    query: str
    provider: str
    created_at: str
    result: dict | None
    error: str | None
    selections: list[SelectionOut]


class AssociationOut(BaseModel):
    id: int
    subquestion_id: int
    source_id: int
    origin: str  # selected_from_search | relevance_assessment
    passage_ids: list[str]
    explanation: str | None
    verification: str  # always 'unverified' in Phase 2
    created_at: str


class ModelCallOut(BaseModel):
    id: int
    agent: str
    model: str
    attempt: int
    created_at: str
    latency_ms: int | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None
    finish_reason: str | None
    ok: bool
    error: str | None
    request_id: str | None
    structured_output: bool | None  # whether response_format json_schema was sent with this request


@router.post("/runs/{run_id}/research", response_model=ResearchStarted, status_code=202)
def start_research(
    run_id: RunId,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    services: ResearchServices = Depends(get_services),
    verify: bool = False,
    complete: bool = False,
):
    """Start collection. ?verify=true then drafts and verifies claims; ?complete=true (end to end, implies verify)
    also runs the bounded correction loop and persists the report. All of it shares one budget."""
    _require_run(conn, run_id)
    with db.tx(conn):
        claimed = db.claim_run_for_research(conn, run_id)
        if claimed and (verify or complete):
            db.claim_run_for_verification(conn, run_id, require_collected=False)
        if claimed and complete:
            db.claim_run_for_completion(conn, run_id, require_collected=False)
    if not claimed:
        raise HTTPException(409, f"research already started for run {run_id} (status: {db.get_run(conn, run_id)['status']})")
    background.add_task(execute_research, run_id, services, verify, complete)
    return ResearchStarted(run_id=run_id, status="researching")


@router.post("/runs/{run_id}/verify", response_model=ResearchStarted, status_code=202)
def start_verification(
    run_id: RunId,
    background: BackgroundTasks,
    conn: sqlite3.Connection = Depends(get_db),
    services: ResearchServices = Depends(get_verify_services),
):
    """Draft and verify claims from an already collected run. No search or fetching happens."""
    run = _require_run(conn, run_id)
    with db.tx(conn):
        claimed = db.claim_run_for_verification(conn, run_id, require_collected=True)
    if not claimed:
        if run["verify_status"] != "not_started":
            raise HTTPException(409, f"verification already started for run {run_id} (verify_status: {run['verify_status']})")
        raise HTTPException(409, f"run {run_id} has no finished collection with sources (status: {run['status']})")
    background.add_task(execute_verification, run_id, services)
    return ResearchStarted(run_id=run_id, status="verifying")


@router.get("/runs/{run_id}/research", response_model=ResearchStatus)
def research_status(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    run = _require_run(conn, run_id)
    by_status: dict[str, int] = {}
    for sq in db.list_subquestions(conn, run_id):
        by_status[sq["status"]] = by_status.get(sq["status"], 0) + 1
    s = get_settings()
    limits = Budget(s).snapshot()
    return ResearchStatus(
        run_id=run_id, status=run["status"], stop_reason=run["stop_reason"], updated_at=run["updated_at"],
        subquestions_by_status=by_status, counts=db.research_counts(conn, run_id),
        model_usage=db.model_usage_totals(conn, run_id),
        limits={k: v[1] for k, v in limits.items()},
    )


@router.get("/runs/{run_id}/subquestions", response_model=list[SubQuestionOut])
def list_subquestions(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    return db.list_subquestions(conn, run_id)


@router.get("/runs/{run_id}/searches", response_model=list[SearchOut])
def list_searches(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    return db.list_searches(conn, run_id)


@router.get("/runs/{run_id}/model-calls", response_model=list[ModelCallOut])
def list_model_calls(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    _require_run(conn, run_id)
    return db.list_model_calls(conn, run_id)


@router.get("/runs/{run_id}/associations", response_model=list[AssociationOut])
def list_associations(run_id: RunId, conn: sqlite3.Connection = Depends(get_db)):
    """Which collected sources were linked to which subquestion. All links are unverified."""
    _require_run(conn, run_id)
    return db.list_associations(conn, run_id)
