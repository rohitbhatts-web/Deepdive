"""Create (and back up) the hosted demo database from the committed, sanitized seed.

    python -m backend.demo_seed                 # create DATABASE_PATH from demo/seed.sql.gz if it does not exist; an existing file is NEVER touched
    python -m backend.demo_seed --reset         # move an existing database aside (kept as *.replaced-<time>) and recreate it from the seed
    python -m backend.demo_seed --backup DEST   # consistent copy of the live database (SQLite online backup) to DEST

The seed is made by scripts/build_demo_seed.py from a private run database after withholding third-party text; this module only loads it.
"""

import argparse
import gzip
import sqlite3
import sys
import time
from pathlib import Path

from backend import db
from backend.config import get_settings
from backend.demo_update import ensure_ledger, run_key


def ensure_demo_db(db_path: str | Path, seed_path: str | Path, *, reset: bool = False) -> str:
    """Return 'exists' (left untouched), 'created' or 'reset'. The file is built beside the target and renamed into place, so a crash leaves no half database."""
    target, seed = Path(db_path), Path(seed_path)
    if target.exists() and not reset:
        return "exists"
    if not seed.exists():
        raise FileNotFoundError(f"demo seed not found: {seed}")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".building")
    tmp.unlink(missing_ok=True)
    conn = sqlite3.connect(tmp)
    try:
        conn.executescript(gzip.decompress(seed.read_bytes()).decode("utf-8"))
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION}")
        conn.row_factory = None
        ensure_ledger(conn, [run_key(q, c) for q, c in conn.execute("SELECT question, created_at FROM runs ORDER BY id")])  # records '0001-base'
        conn.execute("PRAGMA journal_mode = DELETE")  # a plain file: the read-only connection then needs no -wal/-shm files
        conn.commit()
    finally:
        conn.close()
    status = "created"
    if target.exists():
        target.rename(target.with_name(f"{target.name}.replaced-{time.strftime('%Y%m%dT%H%M%S')}"))
        status = "reset"
    tmp.replace(target)
    return status


def backup(db_path: str | Path, dest: str | Path) -> Path:
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; backups are not overwritten")
    src = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    out = sqlite3.connect(dest)
    try:
        src.backup(out)
    finally:
        out.close()
        src.close()
    return dest


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.demo_seed")
    ap.add_argument("--reset", action="store_true", help="replace the database from the seed (the old file is kept beside it)")
    ap.add_argument("--backup", metavar="DEST", help="write a consistent copy of the database to DEST and exit")
    a = ap.parse_args(argv)
    s = get_settings()
    if a.backup:
        print("backup written:", backup(s.database_path, a.backup))
        return 0
    print(f"{s.database_path}: {ensure_demo_db(s.database_path, s.demo_seed_path, reset=a.reset)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
