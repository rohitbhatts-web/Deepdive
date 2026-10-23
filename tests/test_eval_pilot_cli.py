"""Focused checks of the pilot CLI modes and the end-to-end review sheets. No model, search or fetch is called: every network-facing function is replaced
by a recorder that fails the test if it is reached when it must not be."""

import csv
import json
import sqlite3

import pytest

from backend import db
from backend.config import Settings
from evaluation import __main__ as cli
from evaluation import datasets, e2e_eval, e2e_review, pilot
from tests.conftest import requires_full_evidence
from tests.test_phase4_units import make_report, report_world


@pytest.fixture
def wired(tmp_path, monkeypatch):
    calls = {"verify": 0, "e2e": []}
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(_env_file=None, mesh_api_key=""))
    monkeypatch.setattr(pilot, "require_ready", lambda: None)
    from backend import services
    monkeypatch.setattr(services, "build_services", lambda *a, **k: type("S", (), {"llm": object()})())

    def fake_verify(llm, settings, **kw):
        calls["verify"] += 1
        kw["db_path"].write_bytes(b"db")  # the CLI hands over the path it keeps
        return [], []

    def fake_e2e(split, settings, db_path, out_path, *, confirm, question_ids=None):
        calls["e2e"].append((split, tuple(question_ids or ()), settings.max_model_requests))
        out_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(pilot, "run_verification", fake_verify)
    monkeypatch.setattr(e2e_eval, "execute", fake_e2e)
    return tmp_path, calls


def test_the_pilot_scope_is_twelve_development_claims_and_two_development_questions():
    held = set(datasets._read(datasets.LOCK)["lock"]["claim_examples"]["heldout_ids"])
    assert len(pilot.examples()) == 12 and all(e["split"] == "dev" and e["id"] not in held for e in pilot.examples())
    qs = {q["id"]: q for q in datasets.load_questions()}
    assert pilot.PILOT_QUESTIONS == ["Q02", "Q05"] and all(qs[i]["split"] == "dev" for i in pilot.PILOT_QUESTIONS)


def test_a_dry_run_calls_nothing(wired):
    _, calls = wired
    assert cli.main(["pilot"]) == 0 and calls == {"verify": 0, "e2e": []}


@requires_full_evidence
def test_claims_only_runs_no_end_to_end_work_and_a_second_run_cannot_overwrite_it(wired):
    root, calls = wired
    assert cli.main(["pilot", "--execute", "--claims-only"]) == 0
    assert calls["verify"] == 1 and calls["e2e"] == []
    result = root / "results" / "pilot_verification.json"
    before = result.read_bytes()
    assert cli.main(["pilot", "--execute", "--claims-only"]) == 1
    assert calls["verify"] == 1 and result.read_bytes() == before  # refused before anything was built or sent


def test_e2e_only_runs_just_the_two_development_questions_and_never_the_verification(wired):
    root, calls = wired
    (root / "results").mkdir()
    (root / "results" / "pilot_verification.json").write_text("frozen", encoding="utf-8")  # the frozen claim result must survive untouched
    assert cli.main(["pilot", "--execute", "--e2e-only"]) == 0
    assert calls["verify"] == 0 and calls["e2e"] == [("dev", ("Q02", "Q05"), 40)]
    assert (root / "results" / "pilot_verification.json").read_text(encoding="utf-8") == "frozen"
    assert cli.main(["pilot", "--execute", "--e2e-only"]) == 1 and len(calls["e2e"]) == 1  # no repeat, no overwrite


def test_the_default_budget_arm_is_a_separate_opt_in(wired):
    _, calls = wired
    assert cli.main(["pilot", "--execute", "--e2e-only"]) == 0 and [c[2] for c in calls["e2e"]] == [40]


def test_the_two_modes_exclude_each_other(wired):
    _, calls = wired
    assert cli.main(["pilot", "--execute", "--claims-only", "--e2e-only"]) == 1 and calls == {"verify": 0, "e2e": []}


def test_review_sheets_show_report_claims_and_requirements_but_never_the_pipelines_own_assessment(tmp_path, settings):
    path = tmp_path / "e2e.db"
    conn = db.connect(path)
    r, _ids, _odd = report_world(conn, settings)
    make_report(conn, r, None)
    conn.close()
    files = e2e_review.build(path, [("Q02", r["run"])], tmp_path / "sheet")
    assert [f.name for f in files] == ["sheet_report_claims.md", "sheet_report_claims.csv", "sheet_requirements.md", "sheet_requirements.csv"]
    claims_rows = list(csv.DictReader((tmp_path / "sheet_report_claims.csv").open(encoding="utf-8")))
    req_rows = list(csv.DictReader((tmp_path / "sheet_requirements.csv").open(encoding="utf-8")))
    assert claims_rows and all(x["run_key"] == f"Q02@{r['run']}" and x["human_label"] == "" and x["reviewer"] == "" for x in claims_rows)
    assert [x["requirement_id"] for x in req_rows] == ["R1", "R2", "R3", "R4"] and all(x["human_label"] == "" for x in req_rows)
    md = (tmp_path / "sheet_requirements.md").read_text(encoding="utf-8") + (tmp_path / "sheet_report_claims.md").read_text(encoding="utf-8")
    assert "pipeline's own assessment is deliberately not shown" in md and "AI-assisted human review; not independent or blinded." in md
    assert claims_rows[0]["claim"] in md and "Cited passage text" in md
    for leaked in ("claims_with_gaps", "claims_all_requirements_addressed", "insufficient_evidence", "unresolved", "missing_kind"):
        assert leaked not in md  # nothing the pipeline concluded about its own coverage reaches the reviewer
    assert sqlite3.connect(path).execute("select count(*) from reports").fetchone()[0] == 1  # read-only: nothing was written to the run database


def test_review_sheets_refuse_a_held_out_question(tmp_path, settings):
    path = tmp_path / "e2e.db"
    conn = db.connect(path)
    r, _ids, _odd = report_world(conn, settings)
    make_report(conn, r, None)
    conn.close()
    with pytest.raises(SystemExit, match="held-out"):
        e2e_review.build(path, [("Q09", r["run"])], tmp_path / "x")
    assert not list(tmp_path.glob("x_*"))


def test_result_keys_match_what_the_result_builder_looks_up():
    q = next(x for x in datasets.load_questions() if x["id"] == "Q02")
    assert f"{q['id']}@7" == "Q02@7" and json.dumps(q["answer_requirements"][0]["id"]) == '"R1"'


# --- CLI-to-job integration (stubbed providers; a real graph job on a temporary evaluation database) -------------------------------------------
# Regression for the Pilot B crash: the run was created in the evaluation database but the job opened the application's default database, where that
# run id did not exist, so the first event write failed on its foreign key and the failure handler then failed again on the missing run.

def _stub_services(monkeypatch, settings, fetcher, mesh, search, *, ignore_settings_path=None):
    from backend import services
    from backend.llm import LLM

    def build(s, mode="collect"):
        path = ignore_settings_path or s.database_path  # the real build_services opens settings.database_path
        return services.ResearchServices(s, LLM(mesh, s, frozenset()), search, fetcher, lambda: db.connect(path))

    monkeypatch.setattr(services, "build_services", build)


def _world(tmp_path, monkeypatch, settings):
    app_db = tmp_path / "app.db"
    s = settings.model_copy(update={"database_path": str(app_db)})
    monkeypatch.setattr(cli, "ROOT", tmp_path)
    monkeypatch.setattr(cli, "get_settings", lambda: s)
    monkeypatch.setattr(pilot, "require_ready", lambda: None)
    return s, app_db


def _scripted():
    from tests.conftest import A, B, C
    from tests.fakes import FakeMesh, FakeSearch, plan, queries, select
    mesh = FakeMesh(planner=plan(("What is the size?", ["a number with a unit"]), ("Who made it?", ["a named organisation"])),
                    queries=[queries("size query"), queries("maker query")], select=[select(A), select(B, C)])
    search = FakeSearch({"size query": [(A, "Alpha", "alpha snippet")], "maker query": [(B, "Beta", "b"), (C, "Gamma", "g")]})
    return mesh, search


def test_pilot_cli_runs_the_job_in_the_evaluation_database_and_leaves_the_application_database_untouched(tmp_path, monkeypatch, settings, fetcher):
    s, app_db = _world(tmp_path, monkeypatch, settings)
    mesh, search = _scripted()
    _stub_services(monkeypatch, s, fetcher, mesh, search)
    assert cli.main(["pilot", "--execute", "--e2e-only"]) == 0
    ev = sqlite3.connect(tmp_path / "results" / "pilot_e2e_evaluation_budget.db")
    assert ev.execute("select count(*) from runs").fetchone()[0] == 2  # Q02 and Q05, each its own run
    events = {t for (t,) in ev.execute("select event_type from audit_events")}
    assert {"run_created", "research_started"} <= events  # the job reached its first event: the run was visible to it
    assert ev.execute("select count(*) from audit_events where event_type = 'research_started'").fetchone()[0] == 2
    assert ev.execute("select count(*) from model_calls").fetchone()[0] > 0 and mesh.steps()[0] == "planner"
    assert not any(st == "researching" for (st,) in ev.execute("select status from runs"))  # every job finished or failed cleanly, none left claimed
    assert (tmp_path / "results" / "pilot_e2e_evaluation_budget.json").exists()
    if app_db.exists():  # the application database may not even have been created; if it was, it holds nothing
        app = sqlite3.connect(app_db)
        assert all(app.execute(f"select count(*) from {t}").fetchone()[0] == 0 for t in ("runs", "audit_events", "model_calls", "searches"))


def test_a_job_pointed_at_a_different_database_is_refused_before_any_provider_call(tmp_path, monkeypatch, settings, fetcher):
    s, _app_db = _world(tmp_path, monkeypatch, settings)
    mesh, search = _scripted()
    other = tmp_path / "elsewhere.db"
    _stub_services(monkeypatch, s, fetcher, mesh, search, ignore_settings_path=other)  # a services object that ignores the evaluation database
    with pytest.raises(RuntimeError, match="not visible to the job's database"):
        cli.main(["pilot", "--execute", "--e2e-only"])
    assert mesh.steps() == []  # nothing was sent to any provider
    assert sqlite3.connect(other).execute("select count(*) from runs").fetchone()[0] == 0


def test_a_failed_attempt_is_kept_and_the_next_attempt_writes_its_own_files(tmp_path, monkeypatch, settings, fetcher):
    s, _ = _world(tmp_path, monkeypatch, settings)
    (tmp_path / "results").mkdir()
    failed = tmp_path / "results" / "pilot_e2e_evaluation_budget.db"
    failed.write_bytes(b"failed attempt")
    assert cli.main(["pilot", "--execute", "--e2e-only"]) == 1 and failed.read_bytes() == b"failed attempt"  # protection stays on
    mesh, search = _scripted()
    _stub_services(monkeypatch, s, fetcher, mesh, search)
    assert cli.main(["pilot", "--execute", "--e2e-only", "--attempt", "2"]) == 0
    assert failed.read_bytes() == b"failed attempt" and (tmp_path / "results" / "pilot_e2e_evaluation_budget_attempt2.db").exists()


def test_the_failure_handler_keeps_the_original_error_when_the_run_does_not_exist(tmp_path, settings, fetcher):
    from backend.graph import execute_research
    from backend.llm import LLM
    from backend.services import ResearchServices

    mesh, search = _scripted()
    path = tmp_path / "empty.db"
    svc = ResearchServices(settings, LLM(mesh, settings, frozenset()), search, fetcher, lambda: db.connect(path))
    with pytest.raises(sqlite3.IntegrityError, match="FOREIGN KEY"):  # the original error, not a TypeError from the handler
        execute_research(999, svc)
    assert mesh.steps() == []
