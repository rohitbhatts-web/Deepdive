"""The hosted recorded demo: one origin, read-only enforcement, the sanitized seed, persistence. No network, no model, no provider key."""

import hashlib
import sqlite3
from pathlib import Path
from urllib.parse import urlparse

import pytest
from fastapi.testclient import TestClient

from backend import db, mesh_client, services
from backend import demo_seed as seed_mod
from backend.config import Settings
from backend.main import create_app
from backend.public import create_public_app
from backend.readonly import ReadOnlyError

ROOT = Path(__file__).parent.parent
ALLOWED = {"en.wikipedia.org", "datatracker.ietf.org", "www.rfc-editor.org", "rfc-editor.org", "www.postgresql.org"}


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture
def demo(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True)
    (dist / "index.html").write_text("<!doctype html><title>DeepDive</title><div id=root></div>", encoding="utf-8")
    (dist / "assets" / "app-abc123.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside the build", encoding="utf-8")
    s = Settings(_env_file=None, public_read_only=True, database_path=str(tmp_path / "vol" / "deepdive.db"), frontend_dist=str(dist),
                 demo_seed_path=str(ROOT / "demo" / "seed.sql.gz"), demo_info_path=str(ROOT / "demo" / "demo_info.json"), mesh_api_key="")
    for mod in ("backend.api.runs", "backend.mesh_client"):
        monkeypatch.setattr(f"{mod}.get_settings", lambda s=s: s)
    app = create_public_app(s, create_app(s))
    with TestClient(app) as client:
        client.settings, client.db_file = s, Path(s.database_path)
        yield client


def test_one_origin_serves_the_build_the_api_and_deep_links(demo):
    assert demo.get("/health").json() == {"status": "ok"}
    ready = demo.get("/ready")
    assert ready.status_code == 200 and ready.json() == {"status": "ready", "runs": 3, "update_error": None}  # the seed plus the pending update package
    index = demo.get("/")
    assert index.status_code == 200 and "text/html" in index.headers["content-type"] and "DeepDive" in index.text
    for deep in ("/runs/1/report", "/runs/1/evidence", "/runs/2", "/anything/else"):  # refresh on a deep link reaches the client router
        r = demo.get(deep)
        assert r.status_code == 200 and "<div id=root>" in r.text, deep
    asset = demo.get("/assets/app-abc123.js")
    assert asset.status_code == 200 and "immutable" in asset.headers["cache-control"] and demo.get("/").headers["cache-control"] == "no-cache"
    assert demo.get("/api/runs").status_code == 200 and demo.get("/api/does-not-exist").status_code == 404
    assert "<div id=root>" not in demo.get("/api/does-not-exist").text  # an unknown API path is never answered with the page
    for h in ("content-security-policy", "x-content-type-options", "x-frame-options"):
        assert h in demo.get("/").headers
    assert "unsafe-eval" not in demo.get("/").headers["content-security-policy"] and "script-src 'self'" in demo.get("/").headers["content-security-policy"]


def test_a_path_outside_the_build_is_never_served(demo):
    for p in ("/../secret.txt", "/%2e%2e/secret.txt", "/assets/../../secret.txt"):
        r = demo.get(p)
        assert "outside the build" not in r.text, p


def test_the_api_serves_the_recorded_runs_and_labels_itself_a_demo(demo):
    info = demo.get("/api/demo").json()
    assert info["read_only"] is True and "Recorded" in info["label"] and {r["run_id"] for r in info["runs"]} == {1, 2, 3} and info["limitations"]
    health = demo.get("/api/health").json()
    assert health["read_only"] is True and health["mesh_configured"] is False
    runs = demo.get("/api/runs").json()
    assert runs["total"] == 3
    report = demo.get("/api/runs/1/report").json()
    assert report["markdown"] and report["outcome"]
    assert demo.get("/api/docs").status_code == 404 and demo.get("/api/openapi.json").status_code == 404 and demo.get("/docs").status_code in (200, 404)


def test_every_mutation_of_the_full_api_is_refused_and_nothing_changes(demo):
    before = sha(demo.db_file)
    full = create_app(Settings(_env_file=None)).openapi()["paths"]  # the writable API: every non-GET operation it has
    writes = [(m.upper(), p.replace("{run_id}", "1").replace("{claim_id}", "1").replace("{report_id}", "1")) for p, item in full.items() for m in item if m != "get"]
    assert len(writes) >= 6  # POST /runs, /research, /verify, /complete, /retry, /sources
    for method, path in writes + [("PUT", "/runs/1"), ("PATCH", "/runs/1"), ("DELETE", "/runs/1"), ("POST", "/runs/1/research?complete=true")]:
        r = demo.request(method, f"/api{path}", json={"question": "x", "confirm_paid_retry": True, "url": "https://example.com"})
        assert r.status_code == 403, (method, path, r.status_code)
        assert r.json()["detail"]["error"] == "read_only", (method, path)
    assert demo.post("/api/runs", json={"question": "a new question"}).status_code == 403
    assert demo.get("/api/runs").json()["total"] == 3
    assert sha(demo.db_file) == before  # the database file is byte-identical


def test_the_database_is_opened_read_only_and_no_provider_client_can_be_built(demo):
    conn = db.connect(demo.db_file, read_only=True)
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("UPDATE runs SET question = 'changed'")
    conn.close()
    with pytest.raises(ReadOnlyError):
        mesh_client.get_mesh_client()
    with pytest.raises(ReadOnlyError):
        mesh_client.get_mesh_async_client()
    with pytest.raises(ReadOnlyError):
        services.build_services(demo.settings, mode="all")


def test_the_seed_publishes_only_cleared_sources_and_markers_stand_in_for_the_rest(demo):
    conn = sqlite3.connect(f"file:{demo.db_file.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    published, withheld = 0, 0
    for s in conn.execute("SELECT id, final_url, snapshot FROM sources"):
        if urlparse(s["final_url"]).netloc in ALLOWED:
            published += 1
            assert not s["snapshot"].startswith("[Text withheld")
            assert all(p["text"] in s["snapshot"] for p in conn.execute("SELECT text FROM passages WHERE source_id = ?", (s["id"],)))
        else:
            withheld += 1
            assert s["snapshot"].startswith("[Text withheld in the public demo")  # URL, title and hash are kept; the text is not
            assert {p["text"].split(":")[0] for p in conn.execute("SELECT text FROM passages WHERE source_id = ?", (s["id"],))} == {"[Text withheld in the public demo"}
    assert (published, withheld) == (5, 5)  # run 1: 3 of 8 pages published; the development check adds 2 RFC pages
    for q in conn.execute("SELECT ce.quote, s.final_url FROM claim_evidence ce JOIN passages p ON p.id = ce.passage_id JOIN sources s ON s.id = p.source_id"):
        assert (urlparse(q["final_url"]).netloc in ALLOWED) == (not q["quote"].startswith("[quote withheld"))
    for r in conn.execute("SELECT result_json FROM searches"):
        for hit in __import__("json").loads(r["result_json"] or "{}").get("hits", []):
            if urlparse(hit["url"]).netloc not in ALLOWED:
                assert hit.get("snippet", "[snippet withheld in the public demo]") == "[snippet withheld in the public demo]"
    # the API serves the same: a withheld page's passages are the marker, never the page
    withheld_id = next(s["id"] for s in conn.execute("SELECT id, final_url FROM sources") if urlparse(s["final_url"]).netloc not in ALLOWED)
    body = demo.get("/api/runs/1/passages").json()
    texts = {p["text"] for p in (body["items"] if isinstance(body, dict) else body) if p["source_id"] == withheld_id}
    assert texts and all(t.startswith("[Text withheld") for t in texts)
    conn.close()


def test_the_database_persists_across_restarts_and_the_seed_never_overwrites_it(tmp_path):
    target, seed = tmp_path / "vol" / "deepdive.db", ROOT / "demo" / "seed.sql.gz"
    assert seed_mod.ensure_demo_db(target, seed) == "created"
    first = sha(target)
    assert seed_mod.ensure_demo_db(target, seed) == "exists" and sha(target) == first  # a restart leaves the volume's database alone
    # an in-place change on the volume survives the next start
    c = sqlite3.connect(target)
    c.execute("CREATE TABLE marker (x)")
    c.commit()
    c.close()
    changed = sha(target)
    assert seed_mod.ensure_demo_db(target, seed) == "exists" and sha(target) == changed
    assert seed_mod.ensure_demo_db(target, seed, reset=True) == "reset"
    assert any(p.name.startswith("deepdive.db.replaced-") for p in target.parent.iterdir())  # the old file is kept, not deleted
    assert sqlite3.connect(target).execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION


def test_backup_is_a_consistent_copy_and_never_overwrites(tmp_path):
    target = tmp_path / "deepdive.db"
    seed_mod.ensure_demo_db(target, ROOT / "demo" / "seed.sql.gz")
    out = seed_mod.backup(target, tmp_path / "backups" / "copy.db")
    assert sqlite3.connect(out).execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 2  # the seed alone
    with pytest.raises(FileExistsError):
        seed_mod.backup(target, out)


def test_the_public_entry_point_refuses_to_start_writable(tmp_path):
    s = Settings(_env_file=None, public_read_only=False, database_path=str(tmp_path / "d.db"), frontend_dist=str(tmp_path))
    with pytest.raises(RuntimeError, match="PUBLIC_READ_ONLY=1"), TestClient(create_public_app(s)):
        pass
