"""Ingest one public URL into a run: fetch, extract, split, store. Every outcome is recorded."""

import hashlib
import sqlite3
from dataclasses import dataclass

from backend import db
from backend.config import Settings, get_settings
from backend.extract import extract, split_passages
from backend.web import Fetcher


@dataclass
class IngestResult:
    status: str  # ingested | duplicate | failed
    outcome: str  # ok | duplicate_url | duplicate_content | blocked | timeout | ...
    reason: str | None = None
    source_id: int | None = None
    fetch_attempt_id: int | None = None
    passage_count: int = 0


def _failed(conn, run_id, url, outcome, reason, status_code=None) -> IngestResult:
    with db.tx(conn):
        attempt = db.record_fetch_attempt(conn, run_id, url, outcome, status_code=status_code, reason=reason)
        db.add_event(conn, run_id, "fetch_failed", "ingest", {"url": url, "outcome": outcome, "reason": reason})
    return IngestResult("failed", outcome, reason, fetch_attempt_id=attempt)


def _duplicate(conn, run_id, url, outcome, source) -> IngestResult:
    with db.tx(conn):
        attempt = db.record_fetch_attempt(conn, run_id, url, outcome, source_id=source["id"])
        db.add_event(conn, run_id, "source_duplicate", "ingest",
                     {"url": url, "outcome": outcome, "source_id": source["id"]})
    return IngestResult("duplicate", outcome, source_id=source["id"], fetch_attempt_id=attempt)


def ingest_url(
    conn: sqlite3.Connection, fetcher: Fetcher, run_id: int, url: str, settings: Settings | None = None
) -> IngestResult:
    settings = settings or get_settings()
    existing = db.find_source(conn, run_id, requested_url=url)
    if existing:
        return _duplicate(conn, run_id, url, "duplicate_url", existing)

    fetched = fetcher.fetch(url)
    if not fetched.ok:
        return _failed(conn, run_id, url, fetched.outcome, fetched.reason, fetched.status_code)

    title, snapshot = extract(fetched.body, fetched.content_type or "")
    if not snapshot:
        return _failed(conn, run_id, url, "no_extractable_text", "page contains no readable text",
                       fetched.status_code)

    content_hash = hashlib.sha256(snapshot.encode("utf-8")).hexdigest()
    same = db.find_source(conn, run_id, content_hash=content_hash)
    if same:
        return _duplicate(conn, run_id, url, "duplicate_content", same)

    spans = split_passages(snapshot, settings.passage_target_chars, settings.passage_max_chars)
    try:
        with db.tx(conn):
            source_id = db.insert_source(
                conn, run_id, requested_url=url, final_url=fetched.final_url, title=title,
                content_type=fetched.content_type or "", content_hash=content_hash, snapshot=snapshot,
                passages=spans,
            )
            attempt = db.record_fetch_attempt(
                conn, run_id, url, "ok", status_code=fetched.status_code, source_id=source_id
            )
            db.add_event(conn, run_id, "source_ingested", "ingest", {
                "url": url, "final_url": fetched.final_url, "source_id": source_id,
                "passages": len(spans), "snapshot_chars": len(snapshot), "redirects": fetched.redirects,
            })
    except sqlite3.IntegrityError:
        # A concurrent request stored the same URL or content between our check and our insert.
        # The transaction rolled back; report the winner as a duplicate instead of failing.
        winner = db.find_source(conn, run_id, requested_url=url) or db.find_source(conn, run_id, content_hash=content_hash)
        if winner is None:
            raise
        return _duplicate(conn, run_id, url, "duplicate_content" if winner["requested_url"] != url else "duplicate_url", winner)
    return IngestResult("ingested", "ok", source_id=source_id, fetch_attempt_id=attempt, passage_count=len(spans))
