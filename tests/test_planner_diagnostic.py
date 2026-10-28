"""The prepared planner diagnostic, tested offline with the fixture Mesh: one request at most, a fresh database only, no start without explicit confirmation.
No network and no model call."""

import sys
from pathlib import Path

import pytest

from backend.llm import LLM
from tests.fakes import FakeMesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import planner_diagnostic as pd

QUOTES = ["How do Git merge and Git rebase differ", "when should a developer use each", "Use official Git documentation where possible."]


def plan(asks_covered=(0, 1, 2)):
    return {"asks": [{"quote": q, "label": f"ask {i}"} for i, q in enumerate(QUOTES)],
            "subquestions": [{"question": f"sub {i}", "evidence_requirements": ["a fact"], "rationale": "why", "covers": [i]} for i in asks_covered]}


def llm_for(mesh, settings):
    return LLM(mesh, settings, frozenset())


def test_a_valid_plan_takes_exactly_one_request_and_records_what_the_planner_call_measured(tmp_path, settings):
    mesh = FakeMesh(planner=plan())
    result = pd.diagnose(settings, llm_for(mesh, settings), tmp_path / "diag.db")
    assert result["outcome"] == "plan_created" and len(mesh.calls) == 1
    (call,) = result["model_calls"]
    assert call["agent"] == "planner" and call["ok"] == 1 and call["request_id"] == "req_fixture_1" and call["prompt_tokens"] == 100 and call["finish_reason"] == "stop"
    assert result["budget"]["model_requests"] == [1, 1] and result["budget"]["search_queries"][0] == 0 and result["budget"]["page_fetches"][0] == 0
    text = pd.render(result)
    assert "pricing: unavailable" in text and "request_id=req_fixture_1" in text
    (diag,) = result["diagnostics"].values()
    assert diag["outcome"] == "ok" and diag["deadline_s"] > 0 and diag["transport"] is None and "transport diagnostics for call" in text  # a fixture client has no transport to trace, and says so


def test_the_request_carries_the_real_planner_prompt_schema_and_the_clean_question(tmp_path, settings):
    import json

    from backend.agents.planner import SYSTEM
    from backend.agents.schemas import Plan
    from backend.llm import strict_json_schema

    mesh = FakeMesh(planner=plan())
    pd.diagnose(settings, llm_for(mesh, settings), tmp_path / "diag.db")
    (sent,) = mesh.calls
    system, user = sent["messages"][0]["content"], sent["messages"][1]["content"]
    assert system.startswith(SYSTEM)  # the planner's own system prompt, unchanged
    assert json.dumps(strict_json_schema(Plan), separators=(",", ":")) in system  # and its own output schema
    assert ("<question>\n" + pd.QUESTION + "\n</question>") in user and "Try this question" not in user  # the clean question, not the card's wrapper text
    assert sent["model"] == settings.planner_model and sent["max_tokens"] == settings.max_output_tokens


def test_a_rejected_plan_does_not_get_a_second_request_the_budget_stops_it_before_anything_is_sent(tmp_path, settings):
    mesh = FakeMesh(planner=plan(asks_covered=(0, 1)))  # ask 2 is covered by no subquestion, so the plan is rejected and the real planner would repair it
    result = pd.diagnose(settings, llm_for(mesh, settings), tmp_path / "diag.db")
    assert len(mesh.calls) == 1 and len(result["model_calls"]) == 1  # the repair request was refused, not sent
    assert result["outcome"].startswith("stopped by the budget after 1 request") and "plan_rejected" in result["events"]


class ExplodingMesh(FakeMesh):
    def _create_raw(self, **kwargs):
        raise RuntimeError("something unexpected inside the client")


def test_an_unexpected_failure_is_reported_with_the_recorded_call_not_lost(tmp_path, settings):
    result = pd.diagnose(settings, llm_for(ExplodingMesh(planner=plan()), settings), tmp_path / "diag.db")
    assert result["outcome"].startswith("stopped") is False and "ModelCallError" in result["outcome"]  # the client's failure is wrapped by the shared LLM, as in a real run
    (call,) = result["model_calls"]
    assert call["ok"] == 0 and call["prompt_tokens"] is None and call["completion_tokens"] is None and call["total_tokens"] is None  # unknown stays unknown, never 0
    assert "completion_tokens=None" in pd.render(result) and "completion_tokens=0" not in pd.render(result)


def test_the_diagnostic_only_writes_a_new_database(tmp_path, settings):
    existing = tmp_path / "old.db"
    existing.write_bytes(b"keep me")
    mesh = FakeMesh(planner=plan())
    with pytest.raises(FileExistsError):
        pd.diagnose(settings, llm_for(mesh, settings), existing)
    assert existing.read_bytes() == b"keep me" and mesh.calls == []


def test_the_limits_are_one_request_a_ninety_second_call_and_a_hundred_second_job(settings):
    s = pd.diagnostic_settings(settings.model_copy(update={"max_elapsed_seconds": 300.0}))
    assert s.max_model_requests == 1 and s.max_elapsed_seconds == 100.0 and s.mesh_timeout == 90.0 and s.max_output_tokens == settings.max_output_tokens


def test_existing_output_paths_are_refused_before_any_network_request(tmp_path, capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not reach the network when an output path exists")

    monkeypatch.setattr("backend.mesh_client.get_mesh_client", boom)
    old_db, old_report = tmp_path / "old.db", tmp_path / "old.report.log"
    old_db.write_bytes(b"db")
    assert pd.main(["--confirm-one-paid-request", "--database", str(old_db), "--report", str(tmp_path / "new.report.log")]) == 2
    old_report.write_bytes(b"report")
    assert pd.main(["--confirm-one-paid-request", "--database", str(tmp_path / "new.db"), "--report", str(old_report)]) == 2
    assert old_db.read_bytes() == b"db" and old_report.read_bytes() == b"report" and "already exists" in capsys.readouterr().out
    assert not (tmp_path / "new.db").exists()


def test_it_refuses_to_start_without_the_explicit_confirmation(capsys, monkeypatch):
    def boom(*_a, **_k):
        raise AssertionError("must not reach the network without confirmation")

    monkeypatch.setattr("backend.mesh_client.get_mesh_client", boom)
    assert pd.main([]) == 2
    assert "Refusing to run" in capsys.readouterr().out


def test_a_question_option_replaces_the_default_without_changing_anything_else(tmp_path, monkeypatch, capsys):
    import backend.llm
    import backend.mesh_client
    import backend.services

    seen = {}

    class Catalog(dict):
        pass

    monkeypatch.setattr(backend.mesh_client, "get_mesh_client", lambda: object())
    monkeypatch.setattr(backend.mesh_client, "get_mesh_async_client", lambda: object())
    monkeypatch.setattr(backend.services, "model_catalog", lambda c: Catalog({s: {"supports_structured_output": True} for s in ("qwen/qwen3-235b-a22b-instruct-2507",)}))
    monkeypatch.setattr(backend.services, "check_models", lambda *a, **k: None)
    monkeypatch.setattr(pd, "diagnose", lambda settings, llm, path, question=pd.QUESTION: seen.update(question=question, limits=(settings.max_model_requests, settings.max_elapsed_seconds)) or
                        {"outcome": "x", "model_calls": [], "diagnostics": {}, "events": [], "budget": {}, "database": str(path), "question": question})
    monkeypatch.setattr("backend.config.get_settings", lambda: __import__("backend.config", fromlist=["Settings"]).Settings(_env_file=None, planner_model="qwen/qwen3-235b-a22b-instruct-2507"))
    q = "How do Git merge and Git rebase differ, and when should a developer use each?"
    assert pd.main(["--confirm-one-paid-request", "--database", str(tmp_path / "d.db"), "--report", str(tmp_path / "d.log"), "--question", q]) == 0
    assert seen["question"] == q and seen["limits"] == (1, 100.0)
    seen.clear()
    assert pd.main(["--confirm-one-paid-request", "--database", str(tmp_path / "e.db"), "--report", str(tmp_path / "e.log")]) == 0
    assert seen["question"] == pd.QUESTION  # the default is unchanged
