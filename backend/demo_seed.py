"""Build the hosted demo database OFFLINE, before anything serves it; the read-only server never creates, upgrades or changes it.

    python -m backend.demo_seed --build DEST                 # seed + every update package + the schema upgrade -> a new, checked file at DEST
    python -m backend.demo_seed --migrate-copy SRC DEST      # a checked, upgraded COPY of an existing demo database (SRC is only read)
    python -m backend.demo_seed --backup DEST                # consistent copy of DATABASE_PATH (SQLite online backup) to DEST

The Docker image runs `--build` while it is built, so the deployed file is made before the server starts and is owned by root (the server user can only
read it). The seed is made by scripts/build_demo_seed.py from a private run database after withholding third-party text; this module only loads it.
Every output is written beside DEST and renamed into place after its checks, so a failure leaves no half database; an existing DEST is never overwritten.
"""

import argparse
import gzip
import json
import sqlite3
import sys
import tempfile
from pathlib import Path

from backend import db
from backend.config import get_settings
from backend.demo_update import (
    apply_update,
    ensure_ledger,
    list_packages,
    load_package,
    run_key,
)

# The schema demo/seed.sql.gz and update 0002 were built at (before the schema-9 to -12 tables). A seed that records its own version (newer
# scripts/build_demo_seed.py writes `PRAGMA user_version`) keeps that version instead.
SEED_SCHEMA_VERSION = 8


class BuildError(RuntimeError):
    """The demo database could not be built or failed a check; nothing was written at the destination."""


def _fresh(dest: Path) -> Path:
    if dest.exists():
        raise FileExistsError(f"{dest} already exists; a demo database is never overwritten (build a new file and point DATABASE_PATH at it)")
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".building")
    for leftover in (tmp, tmp.with_name(tmp.name + "-wal"), tmp.with_name(tmp.name + "-shm"), tmp.with_name(tmp.name + "-journal")):
        leftover.unlink(missing_ok=True)
    return tmp


def _upgrade(path: Path) -> None:
    db.connect(path).close()  # the application's own upgrade: it adds the tables of every newer schema; existing rows are untouched


def _finish(tmp: Path, dest: Path) -> dict:
    """Plain rollback journal (a read-only connection then needs no -wal/-shm file), then integrity, foreign keys, version and a read-only open."""
    conn = sqlite3.connect(tmp)
    try:
        conn.execute("PRAGMA journal_mode = DELETE")
        problems = [f"integrity: {r[0]}" for r in conn.execute("PRAGMA integrity_check") if r[0] != "ok"]
        problems += [f"foreign key: {tuple(r)}" for r in conn.execute("PRAGMA foreign_key_check")]
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != db.SCHEMA_VERSION:
            problems.append(f"schema version {version}, expected {db.SCHEMA_VERSION}")
        updates = [r[0] for r in conn.execute("SELECT id FROM demo_updates ORDER BY id")] if conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_updates'").fetchone() else []
    finally:
        conn.close()
    if problems:
        raise BuildError("; ".join(problems))
    ro = db.connect(tmp, read_only=True)  # exactly how the server will open it
    try:
        runs = ro.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
    finally:
        ro.close()
    if not runs:
        raise BuildError("the built database holds no runs")
    tmp.replace(dest)
    return {"database": str(dest), "schema_version": db.SCHEMA_VERSION, "runs": runs, "updates": updates}


def build_demo_db(dest: str | Path, seed_path: str | Path, updates_dir: str | Path) -> dict:
    """The seed at its own schema version, then every update package in id order with the same checks a deployment used, then the upgrade to the
    current schema. An update built for the current schema is applied after the upgrade; any other mismatch refuses the build."""
    dest, seed = Path(dest), Path(seed_path)
    if not seed.exists():
        raise FileNotFoundError(f"demo seed not found: {seed}")
    tmp = _fresh(dest)
    try:
        conn = sqlite3.connect(tmp)
        try:
            conn.executescript(gzip.decompress(seed.read_bytes()).decode("utf-8"))
            if conn.execute("PRAGMA user_version").fetchone()[0] == 0:
                conn.execute(f"PRAGMA user_version = {SEED_SCHEMA_VERSION}")
            ensure_ledger(conn, [run_key(q, c) for q, c in conn.execute("SELECT question, created_at FROM runs ORDER BY id")])  # records '0001-base'
            conn.commit()
        finally:
            conn.close()
        with tempfile.TemporaryDirectory() as backups:  # apply_update always backs up first; these build-time backups are discarded with the temporary file
            for package in list_packages(updates_dir):
                wanted = load_package(package)["schema_version"]
                c = sqlite3.connect(tmp)
                try:
                    have = c.execute("PRAGMA user_version").fetchone()[0]
                finally:
                    c.close()
                if wanted == db.SCHEMA_VERSION and have < wanted:
                    _upgrade(tmp)
                elif wanted != have:
                    raise BuildError(f"{package.name} was built for schema {wanted}; the database is at {have}")
                apply_update(tmp, package, backups)
        _upgrade(tmp)
        return _finish(tmp, dest)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def migrate_copy(src: str | Path, dest: str | Path) -> dict:
    """An upgraded copy of an existing demo database (for example a backup of a deployed volume). SRC is opened read-only and never changed."""
    src = Path(src)
    if not src.is_file():
        raise FileNotFoundError(f"{src} does not exist")
    tmp = _fresh(Path(dest))
    try:
        backup(src, tmp)
        _upgrade(tmp)
        return _finish(tmp, Path(dest))
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


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
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--build", metavar="DEST", help="build a new demo database from the seed and update packages (settings DEMO_SEED_PATH, DEMO_UPDATES_DIR)")
    g.add_argument("--migrate-copy", nargs=2, metavar=("SRC", "DEST"), help="write an upgraded, checked copy of SRC to DEST; SRC is only read")
    g.add_argument("--backup", metavar="DEST", help="write a consistent copy of DATABASE_PATH to DEST and exit")
    a = ap.parse_args(argv)
    s = get_settings()
    if a.backup:
        print("backup written:", backup(s.database_path, a.backup))
    elif a.migrate_copy:
        print(json.dumps(migrate_copy(*a.migrate_copy), indent=1))
    else:
        print(json.dumps(build_demo_db(a.build, s.demo_seed_path, s.demo_updates_dir), indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
