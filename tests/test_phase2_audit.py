"""Tests added by the Phase 2 audit (docs/PHASE2_REVIEW.md). FIXTURE Mesh/search/web unless stated."""

import re
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import config, db
from backend.budget import Budget, BudgetExceeded
from backend.graph import build_graph
from backend.llm import LLM, ModelCallError
from backend.main import app
from backend.search import SearchError, TavilySearch
from tests.conftest import A, B
from tests.fakes import FakeMesh, FakeSearch, plan, queries, select
from tests.test_research_flow import PLAN, happy_mesh, happy_search, start

BACKEND = Path(__file__).parent.parent / "backend"
MESH_SECRET, TAVILY_SECRET = "mesh-SECRET-0123456789", "tvly-SECRET-9876543210"


# --- every model call goes through the shared Mesh client ---------------------------------------------------


def test_only_the_shared_client_module_constructs_openai_clients():
    constructing = [f.name for f in BACKEND.rglob("*.py") if re.search(r"\bOpenAI\(", f.read_text(encoding="utf-8"))]
    calling = [f.name for f in BACKEND.rglob("*.py") if "chat.completions" in f.read_text(encoding="utf-8")]
    importing = [f.name for f in BACKEND.rglob("*.py") if re.search(r"^\s*(from|import) openai", f.read_text(encoding="utf-8"), re.MULTILINE)]
    assert constructing == ["mesh_client.py"] and calling == ["llm.py"] and importing == ["mesh_client.py"]


def test_mesh_client_has_no_hidden_retries_and_a_bounded_timeout(monkeypatch):
    monkeypatch.setenv("MESH_API_KEY", "k-123456")
    config.get_settings.cache_clear()
    try:
        from backend.mesh_client import get_mesh_client

        client = get_mesh_client()
        assert client.max_retries == 0 and client.timeout == 90.0
        assert str(client.base_url).startswith("https://api.meshapi.ai/v1")
    finally:
        config.get_settings.cache_clear()


def test_planner_and_researcher_execute_as_langgraph_nodes(client, research):
    svc = research(happy_mesh(), happy_search())
    conn = db.connect(client.db_path)
    run = db.create_run(conn, "How big is it and who made it?")
    db.claim_run_for_research(conn, run["id"])
    graph = build_graph(svc, conn, Budget(svc.settings))
    order = [next(iter(update)) for update in graph.stream(
        {"run_id": run["id"], "queue": [], "stop_reason": None, "error": None}, stream_mode="updates")]
    assert order == ["planner", "researcher", "researcher", "associate", "finish"]  # one researcher pass per subquestion
    assert svc.llm.client.steps() == ["planner", "queries", "select", "queries", "select"]
    conn.close()


# --- search configuration ------------------------------------------------------------------------------------


def test_tavily_request_disables_generated_answers_and_extras():
    seen = {}

    def handler(request):
        seen.update(__import__("json").loads(request.content))
        return httpx.Response(200, json={"results": []})

    TavilySearch(config.Settings(tavily_api_key="tvly-x-123456"), transport=httpx.MockTransport(handler)).search("q", 5)
    assert seen["include_answer"] is False and seen["include_raw_content"] is False
    assert seen["include_images"] is False and seen["auto_parameters"] is False and seen["search_depth"] == "basic"


# --- limits account for retries and failed requests -------------------------------------------------------------


def test_failed_model_request_is_charged_to_the_budget(conn):
    run = db.create_run(conn, "Q?")
    budget = Budget(config.Settings())
    with pytest.raises(ModelCallError):
        LLM(FakeMesh(queries=RuntimeError("down")), config.Settings()).call(
            conn, run["id"], budget, agent="researcher", model="m", system="Write targeted web search queries",
            user="u", schema=__import__("backend.agents.schemas", fromlist=["QueryPlan"]).QueryPlan)
    assert budget.model_requests == 1


def test_missing_usage_does_not_disable_the_token_limit(conn):
    s = config.Settings(max_output_tokens=1500, max_total_output_tokens=2000)
    run = db.create_run(conn, "Q?")
    budget = Budget(s)
    from backend.agents.schemas import QueryPlan

    mesh = FakeMesh(report_usage=False, queries=queries("a query"))
    llm = LLM(mesh, s)
    kwargs = {"agent": "researcher", "model": "m", "system": "Write targeted web search queries",
              "user": "u", "schema": QueryPlan}
    llm.call(conn, run["id"], budget, **kwargs)
    assert budget.output_tokens == 1500  # assumed worst case, since none was reported
    (row,) = db.list_model_calls(conn, run["id"])
    assert row["completion_tokens"] is None and row["total_tokens"] is None  # stored usage is only what was reported
    llm.call(conn, run["id"], budget, **kwargs)
    with pytest.raises(BudgetExceeded, match="max_total_output_tokens"):
        llm.call(conn, run["id"], budget, **kwargs)


def test_failed_searches_count_toward_the_search_limit(client, research):
    mesh = FakeMesh(planner=PLAN, queries=[queries("query one", "query two"), queries("query three")])
    research(mesh, FakeSearch({"query one": SearchError("x"), "query two": SearchError("y"), "query three": SearchError("z")}),
             max_search_queries=1, corrective_search_reserve=0)
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert (status["status"], status["stop_reason"]) == ("stopped", "max_search_queries reached (1)")
    assert status["counts"]["failed_searches"] == 1  # the fair share gave the first subquestion one search of one; the failed one still counted
    assert status["subquestions_by_status"] == {"collection_incomplete": 2}


def test_failed_page_fetches_count_toward_the_fetch_limit(client, research):
    mesh = FakeMesh(planner=plan(("One question here?", ["x"])), queries=queries("query one"),
                    select=select("http://internal.test/", A))
    research(mesh, FakeSearch({"query one": [("http://internal.test/", "I", "s"), (A, "A", "s")]}), max_page_fetches=1)
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["stop_reason"] == "max_page_fetches reached (1)" and status["counts"]["sources"] == 0


# --- linking and isolation across runs ---------------------------------------------------------------------------


def test_sources_stay_linked_to_subquestions_and_isolated_between_runs(client, research):
    def both_pick_a():
        return (FakeMesh(planner=PLAN, queries=[queries("query one"), queries("query two")], select=[select(A), select(A, B)]),
                FakeSearch({"query one": [(A, "A", "s")], "query two": [(A, "A", "s"), (B, "B", "s")]}))

    research(*both_pick_a())
    run1, _ = start(client)
    research(*both_pick_a())
    run2, _ = start(client)

    subs1 = [s["id"] for s in client.get(f"/runs/{run1}/subquestions").json()]
    subs2 = [s["id"] for s in client.get(f"/runs/{run2}/subquestions").json()]
    assert not set(subs1) & set(subs2)
    for run, subs in ((run1, subs1), (run2, subs2)):
        sels = [x for s in client.get(f"/runs/{run}/searches").json() for x in s["selections"]]
        assert [(x["subquestion_id"], x["url"], x["outcome"]) for x in sels] == [
            (subs[0], A, "ok"), (subs[1], A, "duplicate_url"), (subs[1], B, "ok")]
        assert sels[0]["source_id"] == sels[1]["source_id"]  # the shared page is linked to both subquestions
        own_sources = {s["id"] for s in client.get(f"/runs/{run}/sources").json()}
        assert {x["source_id"] for x in sels} <= own_sources and len(own_sources) == 2
    conn = db.connect(client.db_path)
    assert conn.execute("SELECT count(*) FROM research_selections r JOIN sources s ON s.id = r.source_id "
                        "WHERE s.run_id != r.run_id").fetchone()[0] == 0
    conn.close()


# --- stored statuses on crash and restart ----------------------------------------------------------------------------


def test_unexpected_crash_leaves_consistent_state(client, research):
    mesh = FakeMesh(planner=PLAN, queries=queries("query one"))
    research(mesh, FakeSearch({"query one": RuntimeError("provider bug")}))
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "failed" and status["stop_reason"] == "unexpected RuntimeError: provider bug"
    assert status["subquestions_by_status"] == {"failed": 1, "collection_incomplete": 1}
    last = client.get(f"/runs/{run_id}/events").json()[-1]
    assert (last["event_type"], last["payload"]["status"]) == ("research_finished", "failed")


def test_startup_marks_interrupted_runs_and_records_why(tmp_path, monkeypatch):
    path = tmp_path / "restart.db"
    conn = db.connect(path)
    run = db.create_run(conn, "Was running?")["id"]
    other = db.create_run(conn, "Never started?")["id"]
    db.claim_run_for_research(conn, run)
    sq_open, sq_busy = (db.add_subquestion(conn, run, q, ["x"]) for q in ("Open one?", "Busy one?"))
    db.set_subquestion_status(conn, sq_busy, "researching")
    conn.close()

    monkeypatch.setenv("DATABASE_PATH", str(path))
    config.get_settings.cache_clear()
    try:
        with TestClient(app) as c:  # runs the real lifespan (startup recovery)
            got = c.get(f"/runs/{run}").json()
            assert got["status"] == "failed" and got["stop_reason"].startswith("interrupted")
            assert c.get(f"/runs/{other}").json()["status"] == "created"  # untouched
            statuses = {s["id"]: s["status"] for s in c.get(f"/runs/{run}/subquestions").json()}
            assert statuses == {sq_open: "collection_incomplete", sq_busy: "collection_incomplete"}
            last = c.get(f"/runs/{run}/events").json()[-1]
            assert (last["event_type"], last["stage"]) == ("research_finished", "startup")
            assert c.post(f"/runs/{run}/research").status_code in (409, 503)  # a failed run is not silently restarted
    finally:
        config.get_settings.cache_clear()


# --- credentials never stored or returned ------------------------------------------------------------------------------


@pytest.mark.parametrize("scenario", ["mesh_error", "tavily_401", "unexpected"])
def test_secrets_never_reach_storage_or_api_responses(client, research, settings, web, scenario):
    secrets = {"mesh_api_key": MESH_SECRET, "tavily_api_key": TAVILY_SECRET}
    if scenario == "mesh_error":
        mesh = FakeMesh(planner=RuntimeError(f"401 for key {MESH_SECRET}"))
        search = FakeSearch({})
    elif scenario == "tavily_401":
        mesh = FakeMesh(planner=plan(("Only one here?", ["x"])), queries=queries("query one"))
        search = TavilySearch(settings.model_copy(update=secrets), transport=httpx.MockTransport(
            lambda r: httpx.Response(401, text=f"bad key {TAVILY_SECRET}")))
    else:
        mesh = FakeMesh(planner=plan(("Only one here?", ["x"])), queries=queries("query one"))
        search = FakeSearch({"query one": RuntimeError(f"boom {TAVILY_SECRET} and {MESH_SECRET}")})
    research(mesh, search, **secrets)
    run_id, _ = start(client)

    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "failed" and "[redacted]" in status["stop_reason"]
    pages = [client.get(f"/runs/{run_id}{p}").text for p in
             ("", "/research", "/subquestions", "/searches", "/model-calls", "/events", "/sources", "/passages")]
    pages.append(client.get("/health").text)
    for text in pages:
        assert MESH_SECRET not in text and TAVILY_SECRET not in text
    stored = b"".join(p.read_bytes() for p in Path(client.db_path).parent.glob(Path(client.db_path).name + "*"))
    assert MESH_SECRET.encode() not in stored and TAVILY_SECRET.encode() not in stored
