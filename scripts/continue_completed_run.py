"""Prepare (and, only with --execute, run) a no-search/no-fetch continuation of a finished run on a NEW COPY of its database.

    uv run python scripts/continue_completed_run.py --source-db data/walkthrough-python-lists.db --copy-db data/walkthrough-continuation.db
        PLAN ONLY (default, free): copies the source with the SQLite backup API (the source is opened read-only and is never modified), upgrades the COPY's schema,
        and prints what a continuation would do, with exact limits. No model, search or fetch call is made.
    ... --execute
        PAID: runs the continuation on the copy under the printed limits (search and fetch limits are 0). It refuses to reuse an existing copy.

The recorded run, its report and its budget record stay untouched in the source; the continuation adds rows to the copy only.
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend import asks, continuation, db
from backend.config import get_settings


def make_copy(source: Path, copy: Path) -> None:
    if copy.exists():
        raise SystemExit(f"refusing: {copy} already exists; a continuation always starts from a NEW copy")
    src = sqlite3.connect(f"file:{source.resolve().as_posix()}?mode=ro", uri=True)
    out = sqlite3.connect(copy)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source-db", required=True, type=Path)
    ap.add_argument("--copy-db", required=True, type=Path)
    ap.add_argument("--run-id", type=int, default=1)
    ap.add_argument("--supply-covers", help="for a run planned before ask provenance was recorded: which asks each subquestion serves, as 'subquestion:ask,ask;...' "
                    "(ask positions start at 0). Written to the COPY as a labelled operator-supplied event; never inferred from keywords")
    ap.add_argument("--execute", action="store_true", help="PAID: run the continuation (model requests only; no search, no fetch)")
    a = ap.parse_args()
    make_copy(a.source_db, a.copy_db)
    settings = get_settings().model_copy(update={"database_path": str(a.copy_db)})
    conn = db.connect(a.copy_db)  # upgrades the COPY's schema; the source was only read
    try:
        if a.supply_covers:
            covers = {int(sq): [int(x) for x in asks_.split(",")] for sq, asks_ in (part.split(":") for part in a.supply_covers.split(";"))}
            known = {s["id"] for s in db.list_subquestions(conn, a.run_id)}
            n_asks = len(asks.provenance(conn, a.run_id)["asks"])
            if not set(covers) <= known or any(i >= n_asks for v in covers.values() for i in v):
                raise SystemExit(f"--supply-covers names a subquestion or ask that does not exist (subquestions {sorted(known)}, {n_asks} asks)")
            with db.tx(conn):
                db.add_event(conn, a.run_id, "ask_provenance_supplied", "continuation", {"covers": covers, "supplied_by": "operator",
                                                                                          "note": "this run was planned before ask provenance was recorded"})
        plan = continuation.plan(conn, a.run_id, settings)
    finally:
        conn.close()
    print(json.dumps(plan, indent=1, default=str))
    if not a.execute:
        print(f"\nPLAN ONLY. Copy ready at {a.copy_db}. Nothing was sent to any provider. Re-run with --execute to spend model requests.")
        return 0 if plan["allowed"] else 1
    if not plan["allowed"]:
        print("refusing: " + "; ".join(plan["reasons"]), file=sys.stderr)
        return 1
    from backend.graph import execute_completion
    from backend.services import build_services

    s = continuation.continuation_settings(settings)
    svc = build_services(s, mode="complete")  # checks configuration and model availability (one catalog request, no inference)
    conn = db.connect(a.copy_db)
    try:
        started = continuation.start(conn, a.run_id, s)
    finally:
        conn.close()
    execute_completion(a.run_id, svc, verify_first=False, round_base=started["round_base"])
    print(f"continuation finished on {a.copy_db}; open it with DATABASE_PATH={a.copy_db}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
