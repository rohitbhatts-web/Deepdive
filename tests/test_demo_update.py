"""Versioned updates of the hosted-demo database: the A1/A2 v5 development check is added as run 3, existing runs are untouched, a backup is made first,
and a second import (same update, or the same run after the ledger was lost) is refused. The schema-8 package is applied before the schema upgrade, by the
offline build; an existing deployment's database is upgraded only as a copy; the read-only server writes nothing. No network, no model call."""

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from backend import db
from backend import demo_seed as seed_mod
from backend import demo_update as upd
from backend.config import Settings
from backend.main import create_app
from backend.public import create_public_app

ROOT = Path(__file__).parent.parent
SEED = ROOT / "demo" / "seed.sql.gz"
UPDATES = ROOT / "demo" / "updates"
PACKAGE = UPDATES / "0002-a1a2-v5.json.gz"
ALLOWED = {"en.wikipedia.org", "datatracker.ietf.org", "www.rfc-editor.org", "rfc-editor.org", "www.postgresql.org"}
CLAIM = "The HTTP/2 specification was published as RFC 9113 in June 2022."


def raw_base(path: Path) -> Path:
    """A database exactly as the first deployment made it: the seed only, at the seed's schema version 8, no update ledger."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript(gzip.decompress(SEED.read_bytes()).decode("utf-8"))
    conn.execute(f"PRAGMA user_version = {seed_mod.SEED_SCHEMA_VERSION}")
    conn.commit()
    conn.close()
    return path


def rows(path: Path) -> dict:
    """Every row of every data table, as comparable strings (the update ledger, whose times differ, is excluded)."""
    conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    out = {}
    for (t,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name <> 'demo_updates' ORDER BY name").fetchall():
        out[t] = sorted(map(repr, conn.execute(f"SELECT * FROM {t}").fetchall()))
    conn.close()
    return out


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def volume(tmp_path):
    return tmp_path / "vol"


def settings_for(volume: Path, **kw) -> Settings:
    dist = volume.parent / "dist"
    dist.mkdir(exist_ok=True)
    (dist / "index.html").write_text("<div id=root></div>", encoding="utf-8")
    return Settings(_env_file=None, public_read_only=True, database_path=str(volume / "deepdive.db"), frontend_dist=str(dist), demo_seed_path=str(SEED),
                    demo_info_path=str(ROOT / "demo" / "demo_info.json"), demo_updates_dir=str(ROOT / "demo" / "updates"), mesh_api_key="", **kw)


def test_the_package_is_sanitized_and_internally_consistent():
    package = upd.load_package(PACKAGE)
    assert package["id"] == "0002-a1a2-v5" and package["schema_version"] == seed_mod.SEED_SCHEMA_VERSION < db.SCHEMA_VERSION and len(package["runs"]) == 1
    assert package["sanitization"]["withheld_sources"] == [] and package["sanitization"]["scrubbed_runs"] == []
    sources = package["tables"]["sources"]
    assert {urlparse(s["final_url"]).netloc for s in sources} <= ALLOWED  # only cleared sources carry text
    snapshot = {s["id"]: s["snapshot"] for s in sources}
    for p in package["tables"]["passages"]:
        assert p["text"] in snapshot[p["source_id"]] and p["id"].startswith(f"S{p['source_id']}-P")  # passage ids follow the shifted source ids
    assert min(s["id"] for s in sources) == 9 and package["tables"]["runs"][0]["id"] == 3  # ids start past the base seed's
    assert [c["text"] for c in package["tables"]["claims"]] == [CLAIM, CLAIM]
    tampered = json.loads(gzip.decompress(PACKAGE.read_bytes()))
    tampered["tables"]["claims"][0]["text"] = "an edited claim"
    bad = PACKAGE.parent.parent.parent / ".pytest-tampered.json.gz"
    try:
        bad.write_bytes(gzip.compress(json.dumps(tampered).encode()))
        with pytest.raises(upd.UpdateError, match="does not match its recorded hash"):
            upd.load_package(bad)
    finally:
        bad.unlink(missing_ok=True)


def test_applying_adds_run_3_keeps_runs_1_and_2_byte_for_byte_and_backs_up_first(volume):
    path = raw_base(volume.parent / "pre-ledger.db")  # the deployed state: a seed-made database without a ledger
    before = rows(path)
    result = upd.apply_update(path, PACKAGE, volume.parent / "backups")
    assert result["applied"] == "0002-a1a2-v5" and result["runs"] == [3]
    after = rows(path)
    assert all(set(before[t]) <= set(after[t]) for t in before)  # every existing row is still there, byte for byte
    assert {t: len(after[t]) - len(before[t]) for t in before if len(after[t]) != len(before[t])}.get("runs") == 1  # and the only additions belong to the new run
    assert len(after["runs"]) == 3 and len(after["audit_events"]) == len(before["audit_events"]) + 4
    conn = sqlite3.connect(path)
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == [] and conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert [r[0] for r in conn.execute("SELECT id FROM demo_updates ORDER BY id")] == ["0001-base", "0002-a1a2-v5"]  # the pre-ledger database was recognised as the base
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    backup = Path(result["backup"])
    assert backup.exists() and backup.parent == volume.parent / "backups"
    b = sqlite3.connect(backup)
    assert b.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2 and b.execute("PRAGMA integrity_check").fetchone()[0] == "ok"  # taken BEFORE the import
    assert b.execute("SELECT COUNT(*) FROM sqlite_master WHERE name='demo_updates'").fetchone()[0] in (0, 1)
    b.close()
    conn.close()


def test_the_recorded_claims_verdicts_roles_and_gaps_are_preserved_through_the_api(volume):
    path = raw_base(volume.parent / "d.db")
    upd.apply_update(path, PACKAGE, volume.parent / "backups")
    served = volume.parent / "served.db"
    seed_mod.migrate_copy(path, served)
    s = settings_for(volume)
    s = s.model_copy(update={"database_path": str(served)})
    import backend.api.runs as runs_mod
    import backend.mesh_client as mc
    runs_mod.get_settings, mc.get_settings = (lambda: s), (lambda: s)
    try:
        with TestClient(create_public_app(s, create_app(s))) as c:
            assert c.get("/api/runs").json()["total"] == 3
            claims = c.get("/api/runs/3/claims").json()
            assert [(x["id"], x["text"]) for x in claims] == [(7, CLAIM), (8, CLAIM)]
            report = c.get("/api/runs/3/report").json()
            assert report["outcome"] == "claims_with_gaps" and report["relevance_judged"] is True and report["verifier_rubrics"] == ["attribution-v5"]
            first, second = report["sections"]
            assert first["subquestion_text"] == "When was the original HTTP/2 specification first published?" and first["answered"] is False
            assert first["claims"] == [] and [x["text"] for x in first["context"]] == [CLAIM]  # the same claim is context, not an answer, for the ORIGINAL date
            assert first["context"][0]["role"] == "relevant_context" and first["open_requirements"][0]["requirement_key"] == "Q1.R1"
            assert first["open_requirements"][0]["status"] == "unresolved" and "obsoletes RFC 7540" in first["open_requirements"][0]["gap"]
            assert second["subquestion_text"] == "When was the current revision of the HTTP/2 specification published?" and second["answered"] is True
            assert [x["text"] for x in second["claims"]] == [CLAIM] and second["claims"][0]["role"] == "direct_answer" and second["context"] == []
            assert {r["requirement_key"]: r["status"] for r in report["requirements"]} == {"Q1.R1": "unresolved", "Q2.R1": "addressed"}
            assert [r["requirement_key"] for r in report["unresolved_requirements"]] == ["Q1.R1"]
            cited = [cit["passage_id"] for x in (first["context"][0], second["claims"][0]) for cit in x["citations"]]
            assert cited == ["S9-P0", "S10-P0"]  # citations point at the shifted passage ids
            passages = {p["id"]: p["text"] for p in c.get("/api/runs/3/passages").json()}
            assert set(passages) == {"S9-P0", "S10-P0"} and all("RFC 9113" in t for t in passages.values())
            for cl in claims:  # the recorded verdicts: integrity passed and a supported semantic verdict from the attribution-v5 verifier
                d = c.get(f"/api/runs/3/claims/{cl['id']}").json()
                assert d["integrity_verdict"] == "passed" and d["semantic_verdict"] == "supported" and d["rubric_current"] is True and d["needs_rejudge"] is False
                assert d["provenance"] and "relevance_check" in d["provenance"]  # labelled as a controlled development check in the data itself
            demo = c.get("/api/demo").json()
            assert [r["run_id"] for r in demo["runs"]] == [1, 2, 3] and demo["runs"][2]["title"].startswith("Controlled development check")
            ready = c.get("/ready").json()
            assert ready == {"status": "ready", "runs": 3}
            assert c.post("/api/runs/3/retry", json={"confirm_paid_retry": True}).status_code == 403
    finally:
        import backend.config as cfg
        runs_mod.get_settings, mc.get_settings = cfg.get_settings, cfg.get_settings


def test_a_second_import_is_refused_by_the_ledger_and_by_content_and_changes_nothing(volume):
    path = raw_base(volume.parent / "d.db")
    backups = volume.parent / "backups"
    upd.apply_update(path, PACKAGE, backups)
    after_first, files = sha(path), sorted(p.name for p in backups.iterdir())
    with pytest.raises(upd.AlreadyApplied):
        upd.apply_update(path, PACKAGE, backups)
    assert sha(path) == after_first and sorted(p.name for p in backups.iterdir()) == files  # no change and no extra backup
    conn = sqlite3.connect(path)  # the ledger is lost (a restored older backup, a hand edit): the run's content key still refuses it
    conn.execute("DROP TABLE demo_updates")
    conn.commit()
    conn.close()
    lost = sha(path)
    with pytest.raises(upd.DuplicateImport):
        upd.apply_update(path, PACKAGE, backups)
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 3
    assert not (sqlite3.connect(path).execute("SELECT COUNT(*) FROM sqlite_master WHERE name='demo_updates'").fetchone()[0] == 0 and sha(path) != lost)
    assert upd.main(["--apply", str(PACKAGE), "--backup-dir", str(backups)]) in (0, 3)  # the CLI reports a refusal as exit 3 (uses the process settings, which point elsewhere)


def test_a_failed_import_rolls_back_completely(volume):
    path = raw_base(volume.parent / "d.db")
    conn = sqlite3.connect(path)
    conn.execute("INSERT INTO sources (id, run_id, requested_url, final_url, title, retrieved_at, content_type, content_hash, snapshot) VALUES (10, 1, 'u', 'u', 't', 'x', 'text/html', 'h', 's')")
    conn.commit()
    conn.close()
    before = rows(path)
    with pytest.raises(upd.UpdateError, match="already has a row"):
        upd.apply_update(path, PACKAGE, volume.parent / "backups")
    assert rows(path) == before  # nothing from the package is left, including the run and the ledger entry
    assert sqlite3.connect(path).execute("SELECT COUNT(*) FROM demo_updates WHERE id = '0002-a1a2-v5'").fetchone()[0] == 0


def test_a_different_database_and_a_dry_run_are_handled_without_writing(volume, tmp_path):
    other = tmp_path / "other.db"
    c = db.connect(other)
    c.execute("INSERT INTO runs (question, status, created_at, updated_at) VALUES ('something else', 'created', 'a', 'a')")
    c.execute(f"PRAGMA user_version = {seed_mod.SEED_SCHEMA_VERSION}")  # the package's schema, so the base-run check is what refuses it
    c.close()
    with pytest.raises(upd.UpdateError, match="base runs"):
        upd.apply_update(other, PACKAGE, tmp_path / "b")
    path = raw_base(tmp_path / "d.db")
    before = sha(path)
    plan = upd.apply_update(path, PACKAGE, tmp_path / "b2", dry_run=True)
    assert plan["would_apply"] == "0002-a1a2-v5" and plan["runs"] == [3] and sha(path) == before and not (tmp_path / "b2").exists()


def test_an_existing_deployment_upgraded_as_a_copy_matches_a_fresh_build_and_the_original_is_never_changed(tmp_path):
    live = raw_base(tmp_path / "live" / "deepdive.db")  # the deployed volume: the seed at schema 8, then update 0002 applied at a start-up
    upd.apply_update(live, PACKAGE, tmp_path / "live" / "backups")
    deployed = sha(live)
    copy = seed_mod.migrate_copy(live, tmp_path / "upgraded.db")
    assert sha(live) == deployed and not (tmp_path / "live" / "deepdive.db-wal").exists()  # the original is only read
    built = seed_mod.build_demo_db(tmp_path / "fresh.db", SEED, UPDATES)
    assert copy["schema_version"] == built["schema_version"] == db.SCHEMA_VERSION and copy["runs"] == built["runs"] == 3
    assert built["updates"] == copy["updates"] == ["0001-base", "0002-a1a2-v5"]
    assert rows(tmp_path / "upgraded.db") == rows(tmp_path / "fresh.db")  # identical data and ids, including the newer schema's (empty) tables
    with pytest.raises(FileExistsError):
        seed_mod.migrate_copy(live, tmp_path / "upgraded.db")  # never overwritten


def test_the_read_only_server_writes_nothing_at_start_up_and_refuses_a_database_it_would_have_to_change(volume, tmp_path):
    built = tmp_path / "built" / "deepdive.db"
    seed_mod.build_demo_db(built, SEED, UPDATES)
    before = sha(built)
    s = settings_for(volume).model_copy(update={"database_path": str(built)})
    for _ in range(2):  # a start and a restart
        with TestClient(create_public_app(s, create_app(s))) as c:
            assert c.get("/ready").json() == {"status": "ready", "runs": 3}
    assert sha(built) == before and sorted(p.name for p in built.parent.iterdir()) == ["deepdive.db"]  # no journal, WAL, backup or ledger change
    old = raw_base(tmp_path / "old" / "deepdive.db")  # an existing schema-8 volume: refused, not upgraded in place
    old_sha = sha(old)
    s_old = settings_for(volume).model_copy(update={"database_path": str(old)})
    with pytest.raises(RuntimeError, match="older schema"), TestClient(create_public_app(s_old, create_app(s_old))):
        pass
    assert sha(old) == old_sha
    s_missing = settings_for(volume).model_copy(update={"database_path": str(tmp_path / "missing" / "deepdive.db")})
    with pytest.raises(RuntimeError, match="demo database not found"), TestClient(create_public_app(s_missing, create_app(s_missing))):
        pass
    assert not (tmp_path / "missing").exists()  # nothing was created


def test_a_build_with_a_refused_update_writes_nothing(tmp_path):
    updates = tmp_path / "updates"
    updates.mkdir()
    tampered = json.loads(gzip.decompress(PACKAGE.read_bytes()))
    tampered["tables"]["claims"][0]["text"] = "an edited claim"
    (updates / PACKAGE.name).write_bytes(gzip.compress(json.dumps(tampered).encode()))
    dest = tmp_path / "out" / "deepdive.db"
    with pytest.raises(upd.UpdateError, match="does not match its recorded hash"):
        seed_mod.build_demo_db(dest, SEED, updates)
    assert list(dest.parent.iterdir()) == []  # no database and no partial file
