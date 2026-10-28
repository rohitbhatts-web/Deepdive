"""A rejected plan is recorded: its asks, subquestions and validation problems go into the `plan_rejected` event, for the first plan and for a failed repair, and nothing
else does (no prompt, no request data, no credential). The prompt, validator, budgets and the one bounded repair are unchanged. Fixture Mesh only; no network."""

import json

import pytest

from backend import db
from backend.agents import planner
from backend.budget import Budget
from backend.llm import LLM, AgentOutputError
from tests.fakes import FakeMesh

QUESTION = "How do Git merge and Git rebase differ, and when should a developer use each? Use official Git documentation where possible."
DOCS_ASK = "Use official Git documentation where possible."
DOCS_PROBLEM = f"no ask covers this part of the question: {DOCS_ASK!r}"


def plan_dict(asks, tag="a"):
    return {"asks": [{"quote": q, "label": f"label {i}"} for i, q in enumerate(asks)], "subquestions": [
        {"question": f"subquestion {tag} number {i}", "evidence_requirements": ["a concrete fact"], "rationale": f"reason {i}", "covers": [i]} for i in range(len(asks))]}


TWO = ["How do Git merge and Git rebase differ", "when should a developer use each"]
THREE = [*TWO, DOCS_ASK]


def rejections(conn, run_id):
    return [e for e in db.list_events(conn, run_id) if e["event_type"] == "plan_rejected"]


def test_a_rejected_first_plan_is_stored_with_its_asks_subquestions_and_problems_then_the_repair_proceeds(conn, settings):
    run = db.create_run(conn, QUESTION)
    bad, good = plan_dict(TWO, "first"), plan_dict(THREE, "second")
    mesh = FakeMesh(planner=[bad, good])
    ids = planner.run_planner(conn, LLM(mesh, settings), Budget(settings), settings, run)
    assert len(ids) == 3 and len(mesh.calls) == 2  # the one bounded repair is unchanged
    (event,) = rejections(conn, run["id"])
    p = event["payload"]
    assert set(p) == {"attempt", "problems", "rejected_plan"} and p["attempt"] == 1 and p["problems"] == [DOCS_PROBLEM]
    assert p["rejected_plan"] == bad  # exactly the parsed plan the model returned: asks and subquestions with every field
    assert [e["event_type"] for e in db.list_events(conn, run["id"])][-1] == "plan_created"


def test_a_repair_that_is_rejected_too_keeps_both_plans_and_still_fails_as_before(conn, settings):
    run = db.create_run(conn, QUESTION)
    first, second = plan_dict(TWO, "first"), plan_dict([TWO[0], "words that are not in the question"], "second")
    mesh = FakeMesh(planner=[first, second])
    with pytest.raises(AgentOutputError, match="still does not cover"):
        planner.run_planner(conn, LLM(mesh, settings), Budget(settings), settings, run)
    assert len(mesh.calls) == 2 and db.list_subquestions(conn, run["id"]) == []  # no third request; nothing stored for a refused plan
    one, two = rejections(conn, run["id"])
    assert (one["payload"]["attempt"], two["payload"]["attempt"]) == (1, 2)
    assert one["payload"]["rejected_plan"] == first and two["payload"]["rejected_plan"] == second
    assert any("not in the question" in x for x in two["payload"]["problems"])


def test_an_accepted_plan_records_no_rejection(conn, settings):
    run = db.create_run(conn, QUESTION)
    planner.run_planner(conn, LLM(FakeMesh(planner=plan_dict(THREE)), settings), Budget(settings), settings, run)
    assert rejections(conn, run["id"]) == []


def test_the_event_holds_only_the_parsed_plan_no_prompt_request_data_or_credential(conn, settings):
    s = settings.model_copy(update={"mesh_api_key": "TESTVALUE-not-a-real-key-456"})
    run = db.create_run(conn, QUESTION)
    mesh = FakeMesh(planner=[plan_dict(TWO), plan_dict(THREE)])
    planner.run_planner(conn, LLM(mesh, s), Budget(s), s, run)
    (event,) = rejections(conn, run["id"])
    text = json.dumps(event["payload"])
    assert "TESTVALUE-not-a-real-key-456" not in text and planner.SYSTEM[:40] not in text and "JSON Schema" not in text
    plan = event["payload"]["rejected_plan"]
    assert set(plan) == {"asks", "subquestions"}
    assert all(set(a) == {"quote", "label"} for a in plan["asks"])
    assert all(set(q) == {"question", "evidence_requirements", "rationale", "covers"} for q in plan["subquestions"])


def test_the_stored_plan_replays_the_failure_offline(conn, settings):
    run = db.create_run(conn, QUESTION)
    planner.run_planner(conn, LLM(FakeMesh(planner=[plan_dict(TWO), plan_dict(THREE)]), settings), Budget(settings), settings, run)
    (event,) = rejections(conn, run["id"])
    from backend.agents.schemas import Plan

    replay = planner.plan_problems(QUESTION, Plan.model_validate(event["payload"]["rejected_plan"]))
    assert replay == event["payload"]["problems"] == [DOCS_PROBLEM]  # the recorded plan, run through the unchanged validator, gives the recorded reason
