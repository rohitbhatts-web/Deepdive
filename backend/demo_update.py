"""Versioned, backed-up, duplicate-refusing updates of the hosted demo database.

A new recorded run reaches the demo through an UPDATE PACKAGE (`demo/updates/<id>.json.gz`, built by scripts/build_demo_update.py). The offline build
(`python -m backend.demo_seed --build`) applies every package to the seed before the schema upgrade; the read-only server applies nothing. The commands
below still work on an existing database file (for example a copy of an older deployment's volume), never on one a server is using. Applying one:

  1. refuses if its id is already in the `demo_updates` ledger, or if a run with the same content key (question + creation time) already exists
     (so a restored backup or a missing ledger cannot import the run twice);
  2. refuses if the database is not the one the package was built for (schema version, the base runs present);
  3. writes a consistent backup first (SQLite online backup, never overwritten) and checks it;
  4. inserts every row in ONE transaction, with foreign keys enforced and primary-key collisions refused; any failure rolls everything back and the
     database is left exactly as it was;
  5. records the update in the ledger and re-checks integrity. Existing runs and their ids are never modified.

    python -m backend.demo_update --status
    python -m backend.demo_update --dry-run                 # what would be applied, nothing written
    python -m backend.demo_update --apply demo/updates/0002-a1a2-v5.json.gz [--backup-dir /data/backups]
"""

import argparse
import gzip
import hashlib
import json
import sqlite3
import sys
import time
from pathlib import Path

from backend.config import get_settings

LEDGER = """CREATE TABLE IF NOT EXISTS demo_updates (
    id TEXT PRIMARY KEY, title TEXT NOT NULL, applied_at TEXT NOT NULL, package_sha256 TEXT, run_ids TEXT, backup_path TEXT)"""


# Insert-time trigger checks that read other tables without a declared foreign key (found by applying real packages; the update tests exercise all of them).
TRIGGER_DEPENDENCIES = {"report_citations": {"verification_result_passages", "verification_result_evidence", "report_claims"},
                        "report_claim_roles": {"claim_relevance"}, "claim_relevance": {"obligation_assessments", "coverage_obligations"}}


class UpdateError(RuntimeError):
    """The update was refused or failed; the database was not changed."""


class AlreadyApplied(UpdateError):
    """This update id is already in the ledger."""


class DuplicateImport(UpdateError):
    """A run with the same content key already exists."""


def run_key(question: str, created_at: str) -> str:
    return hashlib.sha256(f"{question}\n{created_at}".encode()).hexdigest()


def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def load_package(path: str | Path) -> dict:
    raw = gzip.decompress(Path(path).read_bytes()).decode("utf-8")
    package = json.loads(raw)
    if package.get("format") != 1:
        raise UpdateError(f"{path}: unsupported package format {package.get('format')!r}")
    body = {"tables": package["tables"], "runs": package["runs"]}
    if hashlib.sha256(canonical(body).encode("utf-8")).hexdigest() != package["body_sha256"]:
        raise UpdateError(f"{path}: the package body does not match its recorded hash (corrupted or edited)")
    package["_sha256"] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return package


def list_packages(updates_dir: str | Path) -> list[Path]:
    d = Path(updates_dir)
    return sorted(d.glob("*.json.gz")) if d.is_dir() else []


def ensure_ledger(conn: sqlite3.Connection, base_keys: list[str] | None = None) -> None:
    """Create the ledger. A database made before the ledger existed is recognised as the base seed (and recorded as such) when its runs match `base_keys`."""
    existed = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_updates'").fetchone()
    conn.execute(LEDGER)
    if not existed and base_keys is not None:
        have = [run_key(q, c) for q, c in conn.execute("SELECT question, created_at FROM runs ORDER BY id")]
        if have[: len(base_keys)] == base_keys and conn.execute("SELECT COUNT(*) FROM demo_updates WHERE id = '0001-base'").fetchone()[0] == 0:
            conn.execute("INSERT INTO demo_updates (id, title, applied_at, package_sha256, run_ids, backup_path) VALUES ('0001-base', 'base seed (recognised)', ?, NULL, ?, NULL)",
                         (time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), json.dumps(list(range(1, len(base_keys) + 1)))))


def status(db_path: str | Path) -> dict:
    conn = sqlite3.connect(f"file:{Path(db_path).resolve().as_posix()}?mode=ro", uri=True)
    try:
        has = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_updates'").fetchone()
        applied = [dict(zip(("id", "title", "applied_at", "run_ids"), r, strict=True)) for r in conn.execute("SELECT id, title, applied_at, run_ids FROM demo_updates ORDER BY id")] if has else []
        return {"runs": conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0], "ledger": bool(has), "applied": applied}
    finally:
        conn.close()


def _backup(conn: sqlite3.Connection, backup_dir: Path, update_id: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    dest = backup_dir / f"deepdive-before-{update_id}-{time.strftime('%Y%m%dT%H%M%SZ', time.gmtime())}.db"
    if dest.exists():
        raise UpdateError(f"backup {dest} already exists; backups are never overwritten")
    out = sqlite3.connect(dest)
    try:
        conn.backup(out)
        if out.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise UpdateError("the backup failed its integrity check; nothing was changed")
        if out.execute("SELECT COUNT(*) FROM runs").fetchone()[0] != conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]:
            raise UpdateError("the backup does not hold the same runs as the database; nothing was changed")
    finally:
        out.close()
    return dest


def _insert_order(conn: sqlite3.Connection, tables: dict) -> list[str]:
    """Tables ordered so that every table follows the tables it references (declared foreign keys)."""
    parents = {t: {r[2] for r in conn.execute(f"PRAGMA foreign_key_list({t})") if r[2] in tables and r[2] != t} for t in tables}
    for t, extra in TRIGGER_DEPENDENCIES.items():  # rows the schema's triggers look up at insert time, which no foreign key declares
        if t in parents:
            parents[t] |= {e for e in extra if e in tables}
    order: list[str] = []

    def visit(t: str, seen: tuple = ()) -> None:
        if t in order or t in seen:
            return
        for parent in sorted(parents[t]):
            visit(parent, (*seen, t))
        order.append(t)

    for t in tables:
        visit(t)
    return order


def apply_update(db_path: str | Path, package_path: str | Path, backup_dir: str | Path, *, dry_run: bool = False) -> dict:
    package = load_package(package_path)
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version != package["schema_version"]:
            raise UpdateError(f"schema version {version} is not the package's {package['schema_version']}")
        base_keys = package["requires"]["base_run_keys"]
        if not dry_run:
            ensure_ledger(conn, base_keys)
        elif conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='demo_updates'").fetchone() is None:
            conn.execute("CREATE TEMP TABLE demo_updates (id TEXT, title TEXT, applied_at TEXT, package_sha256 TEXT, run_ids TEXT, backup_path TEXT)")
        if conn.execute("SELECT 1 FROM demo_updates WHERE id = ?", (package["id"],)).fetchone():
            raise AlreadyApplied(f"update {package['id']} is already applied")
        have = {run_key(r["question"], r["created_at"]) for r in conn.execute("SELECT question, created_at FROM runs")}
        for run in package["runs"]:
            if run["key"] in have:
                raise DuplicateImport(f"a run with the same question and creation time as {run['question']!r} ({run['created_at']}) already exists; refusing to import it twice")
        missing = [k for k in base_keys if k not in have]
        if missing:
            raise UpdateError("this database does not contain the base runs the package was built for; refusing to apply it to a different database")
        counts = {t: len(rows) for t, rows in package["tables"].items()}
        if dry_run:
            return {"would_apply": package["id"], "title": package["title"], "rows": counts, "runs": [r["new_run_id"] for r in package["runs"]]}
        backup = _backup(conn, Path(backup_dir), package["id"])
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("PRAGMA defer_foreign_keys = ON")
            for table in _insert_order(conn, package["tables"]):  # parents before children: the schema's triggers check related rows at insert time
                rows = package["tables"][table]
                cols = list(rows[0])
                pk = [r[1] for r in sorted(conn.execute(f"PRAGMA table_info({table})"), key=lambda r: r[5]) if r[5]]
                for row in rows:
                    if pk and conn.execute(f"SELECT 1 FROM {table} WHERE " + " AND ".join(f"{c} = ?" for c in pk), [row[c] for c in pk]).fetchone():
                        raise UpdateError(f"{table} already has a row with key {[row[c] for c in pk]}; the package was built for a different database")
                    conn.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])
            bad = conn.execute("PRAGMA foreign_key_check").fetchall()
            if bad:
                raise UpdateError(f"foreign key check failed after the import ({len(bad)} rows); rolled back")
            conn.execute("INSERT INTO demo_updates (id, title, applied_at, package_sha256, run_ids, backup_path) VALUES (?, ?, ?, ?, ?, ?)",
                         (package["id"], package["title"], time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime()), package["_sha256"],
                          json.dumps([r["new_run_id"] for r in package["runs"]]), str(backup)))
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise UpdateError("integrity check failed after the import; restore the backup " + str(backup))
        return {"applied": package["id"], "rows": counts, "runs": [r["new_run_id"] for r in package["runs"]], "backup": str(backup)}
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m backend.demo_update")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="check every pending package and say what would be applied")
    ap.add_argument("--apply", metavar="PACKAGE", help="apply one package (refuses if already applied or duplicate)")
    ap.add_argument("--backup-dir", help="default: <database directory>/backups")
    a = ap.parse_args(argv)
    s = get_settings()
    backup_dir = Path(a.backup_dir) if a.backup_dir else Path(s.database_path).parent / "backups"
    try:
        if a.status:
            print(json.dumps(status(s.database_path), indent=1))
        elif a.apply:
            print(json.dumps(apply_update(s.database_path, a.apply, backup_dir, dry_run=a.dry_run), indent=1))
        elif a.dry_run:
            for p in list_packages(s.demo_updates_dir):
                try:
                    print(json.dumps(apply_update(s.database_path, p, backup_dir, dry_run=True)))
                except AlreadyApplied:
                    print(json.dumps({"already_applied": p.name}))
        else:
            ap.print_help()
    except UpdateError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
