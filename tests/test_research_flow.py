"""End-to-end Phase 2 flow through the API, with FIXTURE Mesh/search/web (see tests/fakes.py)."""

import httpx
import pytest

from backend import db
from backend.budget import Budget
from backend.graph import build_graph
from backend.llm import LLM
from backend.search import SearchError, SearchFatalError
from backend.services import ResearchServices
from tests.conftest import A, B, C
from tests.fakes import FakeMesh, FakeSearch, plan, queries, select

PLAN = plan(("What is the size?", ["a number with a unit"]), ("Who made it?", ["a named organisation"]))


def start(client, question="How big is it and who made it?"):
    run_id = client.post("/runs", json={"question": question}).json()["id"]
    return run_id, client.post(f"/runs/{run_id}/research")


def happy_mesh():
    return FakeMesh(
        planner=PLAN,
        queries=[queries("size query", "size query two"), queries("maker query")],
        select=[select(A), select(B, C)],
    )


def happy_search():
    return FakeSearch({
        "size query": [(A, "Alpha", "alpha snippet")],
        "size query two": [(A, "Alpha again", "dup"), (C, "Gamma", "g")],
        "maker query": [(B, "Beta", "b"), (C, "Gamma", "g")],
    })


def test_full_flow_persists_everything(client, research):
    mesh, search = happy_mesh(), happy_search()
    research(mesh, search)
    run_id, resp = start(client)
    assert (resp.status_code, resp.json()["status"]) == (202, "researching")

    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "collection_finished" and status["stop_reason"] is None
    assert status["subquestions_by_status"] == {"evidence_collected": 2}

    subs = client.get(f"/runs/{run_id}/subquestions").json()
    assert [(s["question"], s["evidence_requirements"]) for s in subs] == [
        ("What is the size?", ["a number with a unit"]), ("Who made it?", ["a named organisation"])]

    searches = client.get(f"/runs/{run_id}/searches").json()
    assert [(s["query"], s["subquestion_id"], s["provider"]) for s in searches] == [
        ("size query", subs[0]["id"], "fixture-search"), ("size query two", subs[0]["id"], "fixture-search"),
        ("maker query", subs[1]["id"], "fixture-search")]
    assert searches[1]["result"]["hits"][1]["url"] == C and searches[1]["result"]["usage"] == {"credits": 1}
    sel = [(x["subquestion_id"], x["url"], x["outcome"]) for s in searches for x in s["selections"]]
    assert sel == [(subs[0]["id"], A, "ok"), (subs[1]["id"], B, "ok"), (subs[1]["id"], C, "ok")]
    assert all(x["source_id"] and x["reason"] == "fixture reason" for s in searches for x in s["selections"])

    sources = client.get(f"/runs/{run_id}/sources").json()
    assert {s["requested_url"] for s in sources} == {A, B, C}
    assert status["counts"]["sources"] == 3 and status["counts"]["passages"] == len(client.get(f"/runs/{run_id}/passages").json())

    # actual usage: 1 planner + 2 per subquestion = 5 fixture calls of (100, 20, 120) tokens
    assert mesh.steps() == ["planner", "queries", "select", "queries", "select"]
    assert status["model_usage"] == {"model_calls": 5, "failed_calls": 0, "calls_with_usage": 5, "prompt_tokens": 500,
                                     "completion_tokens": 100, "total_tokens": 600}
    calls = client.get(f"/runs/{run_id}/model-calls").json()
    assert [c["agent"] for c in calls] == ["planner", "researcher", "researcher", "researcher", "researcher"]
    assert {c["model"] for c in calls} == {"fixture-planner", "fixture-researcher"}

    events = [e["event_type"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert events[:3] == ["run_created", "research_started", "plan_created"] and events[-1] == "research_finished"
    assert events.count("subquestion_done") == 2


def test_duplicate_start_is_rejected_without_new_work(client, research):
    mesh = happy_mesh()
    research(mesh, happy_search())
    run_id, first = start(client)
    calls_after_first = len(mesh.calls)
    second = client.post(f"/runs/{run_id}/research")
    assert (first.status_code, second.status_code) == (202, 409)
    assert len(mesh.calls) == calls_after_first
    assert client.get(f"/runs/{run_id}/research").json()["status"] == "collection_finished"


def test_claim_is_atomic_at_db_level(conn):
    run = db.create_run(conn, "Q?")
    assert db.claim_run_for_research(conn, run["id"]) is True
    assert db.claim_run_for_research(conn, run["id"]) is False


def test_unknown_run_and_inspection_endpoints(client, research):
    research(happy_mesh(), happy_search())
    assert client.post("/runs/999/research").status_code == 404
    for path in ("research", "subquestions", "searches", "model-calls"):
        assert client.get(f"/runs/999/{path}").status_code == 404
    run_id = client.post("/runs", json={"question": "Not started?"}).json()["id"]
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "created" and status["counts"]["searches"] == 0 and status["model_usage"]["model_calls"] == 0


def test_hallucinated_url_is_rejected(client, research):
    mesh = FakeMesh(planner=plan(("Only one?", ["x"])), queries=queries("q one"),
                    select=select("https://invented.test/never-returned", A))
    research(mesh, FakeSearch({"q one": [(A, "Alpha", "s")]}))
    run_id, _ = start(client)
    events = client.get(f"/runs/{run_id}/events").json()
    rejected = [e for e in events if e["event_type"] == "selection_rejected"]
    assert [e["payload"]["url"] for e in rejected] == ["https://invented.test/never-returned"]
    assert [s["requested_url"] for s in client.get(f"/runs/{run_id}/sources").json()] == [A]


def test_fetch_failures_and_search_failures_are_recorded_and_run_continues(client, research, web):
    web.pages[B] = httpx.ReadTimeout("slow")
    mesh = FakeMesh(planner=plan(("Q one here?", ["x"])), queries=queries("bad query", "q two"),
                    select=select("http://internal.test/admin", B, A))
    search = FakeSearch({
        "bad query": SearchError("HTTP 500"),
        "q two": [("http://internal.test/admin", "Internal", "s"), (B, "Beta", "s"), (A, "Alpha", "s")],
    })
    research(mesh, search)
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "collection_finished" and status["counts"]["failed_searches"] == 1
    searches = client.get(f"/runs/{run_id}/searches").json()
    assert searches[0]["error"] == "HTTP 500" and searches[0]["selections"] == []
    assert [(x["url"], x["outcome"]) for x in searches[1]["selections"]] == [
        ("http://internal.test/admin", "blocked"), (B, "timeout"), (A, "ok")]
    assert status["counts"]["failed_fetches"] == 2


def test_no_search_results_marks_no_evidence(client, research):
    mesh = FakeMesh(planner=plan(("Obscure thing?", ["x"])), queries=queries("nothing here"))
    research(mesh, FakeSearch({}))
    run_id, _ = start(client)
    assert client.get(f"/runs/{run_id}/research").json()["subquestions_by_status"] == {"collection_incomplete": 1}
    assert "select" not in mesh.steps()  # no results, so no URL-selection model call was spent


def test_fatal_search_error_fails_run(client, research):
    mesh = FakeMesh(planner=PLAN, queries=queries("size query"))
    research(mesh, FakeSearch({"size query": SearchFatalError("Tavily rejected the request (HTTP 401)")}))
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "failed" and "401" in status["stop_reason"]
    assert status["subquestions_by_status"] == {"failed": 1, "collection_incomplete": 1}


def test_planner_garbage_fails_run_with_reason(client, research):
    research(FakeMesh(planner=["garbage", "still garbage"]), FakeSearch({}))
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "failed" and "planner failed" in status["stop_reason"]
    assert status["model_usage"]["failed_calls"] == 2 and client.get(f"/runs/{run_id}/subquestions").json() == []


def test_budget_stop_preserves_unresolved_work_and_reason(client, research):
    research(happy_mesh(), happy_search(), max_page_fetches=1)
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert status["status"] == "stopped" and status["stop_reason"] == "max_page_fetches reached (1)"
    assert status["counts"]["sources"] == 1
    assert status["subquestions_by_status"] == {"evidence_collected": 1, "collection_incomplete": 1}
    finished = client.get(f"/runs/{run_id}/events").json()[-1]
    assert finished["event_type"] == "research_finished" and finished["payload"]["budget_used_of_limit"]["page_fetches"] == [1, 1]


@pytest.mark.parametrize("limit,value,expected", [
    ("max_model_requests", 1, "max_model_requests reached (1)"),
    ("max_search_queries", 1, "max_search_queries reached (1)"),
    ("max_total_output_tokens", 40, "max_total_output_tokens reached (40)"),  # fixture calls report 20 each
    ("max_elapsed_seconds", 0, "max_elapsed_seconds reached (0s)"),
])
def test_other_limits_stop_the_run(client, research, limit, value, expected):
    research(happy_mesh(), happy_search(), **{limit: value})
    run_id, _ = start(client)
    status = client.get(f"/runs/{run_id}/research").json()
    assert (status["status"], status["stop_reason"]) == ("stopped", expected)


def test_missing_configuration_gives_503_and_leaves_run_startable(client, monkeypatch):
    from backend import config

    for key in ("MESH_API_KEY", "TAVILY_API_KEY", "PLANNER_MODEL", "RESEARCHER_MODEL"):
        monkeypatch.setenv(key, "")
    config.get_settings.cache_clear()
    try:
        run_id = client.post("/runs", json={"question": "Config check?"}).json()["id"]
        resp = client.post(f"/runs/{run_id}/research")
        assert resp.status_code == 503
        detail = resp.json()["detail"]
        assert detail["error"] == "not_configured"
        assert detail["missing"] == ["MESH_API_KEY", "TAVILY_API_KEY", "PLANNER_MODEL", "RESEARCHER_MODEL"]
        assert client.get(f"/runs/{run_id}/research").json()["status"] == "created"  # not consumed
        assert client.get(f"/runs/{run_id}/searches").json() == []  # and no fake results appeared
    finally:
        config.get_settings.cache_clear()


def _catalog_client(payload):
    from types import SimpleNamespace

    return SimpleNamespace(get=lambda path, cast_to: payload if path == "/models" else None)


CHAT = {"model_type": "text", "supports_completions_api": True, "supports_system_prompt": True}


@pytest.mark.parametrize("shape", ["bare_list", "data_wrapper"])
def test_model_catalog_accepts_both_response_shapes(monkeypatch, settings, shape):
    from backend import services

    rows = [{"id": "fixture-planner", **CHAT}, {"id": "fixture-researcher", **CHAT}]  # FIXTURE catalog rows
    fake = _catalog_client(rows if shape == "bare_list" else {"data": rows})
    monkeypatch.setattr(services, "get_mesh_client", lambda: fake)
    s = settings.model_copy(update={"mesh_api_key": "k-123456", "tavily_api_key": "t-123456"})
    assert services.build_services(s).settings is s


def test_unavailable_model_is_a_configuration_error(monkeypatch, settings):
    from backend import services

    fake = _catalog_client([{"id": "fixture-planner", **CHAT}])
    monkeypatch.setattr(services, "get_mesh_client", lambda: fake)
    s = settings.model_copy(update={"mesh_api_key": "k-123456", "tavily_api_key": "t-123456"})
    with pytest.raises(services.ConfigurationError, match="fixture-researcher"):
        services.build_services(s)


def test_non_chat_model_is_rejected(monkeypatch, settings):
    from backend import services

    rows = [{"id": "fixture-planner", **CHAT}, {"id": "fixture-researcher", **CHAT, "model_type": "image"}]
    monkeypatch.setattr(services, "get_mesh_client", lambda: _catalog_client(rows))
    s = settings.model_copy(update={"mesh_api_key": "k-123456", "tavily_api_key": "t-123456"})
    with pytest.raises(services.ConfigurationError, match="cannot serve chat requests.*fixture-researcher"):
        services.build_services(s)


def test_interrupted_runs_are_failed_on_startup(conn):
    run = db.create_run(conn, "Q?")
    db.claim_run_for_research(conn, run["id"])
    assert db.mark_interrupted_runs(conn) == 1
    assert db.get_run(conn, run["id"])["status"] == "failed"


def test_graph_is_an_explicit_stategraph(conn, settings, fetcher):
    svc = ResearchServices(settings, LLM(FakeMesh(), settings), FakeSearch({}), fetcher, lambda: conn)
    nodes = set(build_graph(svc, conn, Budget(settings)).get_graph().nodes)
    assert {"planner", "researcher", "finish"} <= nodes
