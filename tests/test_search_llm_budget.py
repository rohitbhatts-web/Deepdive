import json

import httpx
import pytest

from backend import db
from backend.agents.schemas import QueryPlan
from backend.budget import Budget, BudgetExceeded
from backend.config import Settings
from backend.llm import LLM, AgentOutputError, ModelCallError, parse_json_object
from backend.search import SearchError, SearchFatalError, TavilySearch
from tests.fakes import FakeMesh, queries

# --- Tavily over a FIXTURE transport ---------------------------------------------------------------


def _tavily(handler, key="tvly-fixture"):
    return TavilySearch(Settings(tavily_api_key=key), transport=httpx.MockTransport(handler))


def test_tavily_request_and_parsing():
    seen = {}

    def handler(request):
        seen["url"], seen["auth"], seen["body"] = str(request.url), request.headers["authorization"], json.loads(request.content)
        return httpx.Response(200, json={
            "results": [{"title": "T", "url": "https://a.test/x", "content": "snip", "score": 0.9}, {"title": "no url"}],
            "usage": {"credits": 1}, "request_id": "r1",
        })

    resp = _tavily(handler).search("hello world", 3)
    assert seen["url"] == "https://api.tavily.com/search" and seen["auth"] == "Bearer tvly-fixture"
    assert seen["body"]["query"] == "hello world" and seen["body"]["max_results"] == 3
    assert [h.url for h in resp.hits] == ["https://a.test/x"] and resp.usage == {"credits": 1}


@pytest.mark.parametrize("status,exc", [(401, SearchFatalError), (432, SearchFatalError), (500, SearchError), (429, SearchError)])
def test_tavily_errors(status, exc):
    with pytest.raises(exc):
        _tavily(lambda r: httpx.Response(status, text="nope")).search("q", 3)


def test_tavily_network_error_and_missing_key():
    def boom(request):
        raise httpx.ConnectError("down")

    with pytest.raises(SearchError):
        _tavily(boom).search("q", 3)
    with pytest.raises(SearchFatalError, match="TAVILY_API_KEY"):
        TavilySearch(Settings(tavily_api_key=""))


# --- budget ------------------------------------------------------------------------------------------


def test_budget_limits():
    s = Settings(max_model_requests=1, max_search_queries=1, max_page_fetches=1, max_total_output_tokens=50)
    b = Budget(s)
    b.charge_model_request(); b.charge_search(); b.charge_fetch()
    for charge, name in [(b.charge_model_request, "max_model_requests"), (b.charge_search, "max_search_queries"),
                         (b.charge_fetch, "max_page_fetches")]:
        with pytest.raises(BudgetExceeded, match=name):
            charge()
    b2 = Budget(s)
    b2.add_output_tokens(50)
    with pytest.raises(BudgetExceeded, match="max_total_output_tokens"):
        b2.charge_model_request()


def test_budget_elapsed_time():
    now = [0.0]
    b = Budget(Settings(max_elapsed_seconds=10), clock=lambda: now[0])
    b.charge_search()
    now[0] = 10.0
    with pytest.raises(BudgetExceeded, match="max_elapsed_seconds"):
        b.charge_search()
    assert b.snapshot()["elapsed_seconds"] == [10.0, 10.0]


# --- LLM wrapper ---------------------------------------------------------------------------------------


@pytest.fixture
def run(conn):
    return db.create_run(conn, "Q?")


def _call(conn, run, mesh, settings=None):
    settings = settings or Settings()
    return LLM(mesh, settings).call(
        conn, run["id"], Budget(settings), agent="researcher", model="fixture-m",
        system="Write targeted web search queries", user="u", schema=QueryPlan,
    )


def test_parse_json_object_tolerates_fences_and_prose():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json_object('Sure! {"a": 1} Hope that helps') == {"a": 1}
    with pytest.raises(ValueError):
        parse_json_object("no json here")


def test_usage_is_recorded_from_response(conn, run):
    mesh = FakeMesh(queries=queries("first query", "second query"))
    out = _call(conn, run, mesh)
    assert out.queries == ["first query", "second query"]
    (row,) = db.list_model_calls(conn, run["id"])
    assert (row["ok"], row["model"], row["prompt_tokens"], row["completion_tokens"], row["total_tokens"]) == (
        True, "fixture-m", 100, 20, 120)
    assert mesh.calls[0]["max_tokens"] == 1500 and mesh.calls[0]["model"] == "fixture-m"


def test_invalid_output_retried_once_then_succeeds(conn, run):
    mesh = FakeMesh(queries=["not json at all", queries("a good query")])
    assert _call(conn, run, mesh).queries == ["a good query"]
    assert [(c["attempt"], c["ok"]) for c in db.list_model_calls(conn, run["id"])] == [(1, False), (2, True)]
    assert "invalid" in mesh.calls[1]["messages"][-1]["content"]


def test_two_invalid_outputs_raise(conn, run):
    mesh = FakeMesh(queries=['{"queries": []}', '{"queries": []}'])
    with pytest.raises(AgentOutputError):
        _call(conn, run, mesh)
    assert len(db.list_model_calls(conn, run["id"])) == 2


def test_api_failure_is_recorded_and_raised(conn, run):
    with pytest.raises(ModelCallError, match="boom"):
        _call(conn, run, FakeMesh(queries=RuntimeError("boom")))
    (row,) = db.list_model_calls(conn, run["id"])
    assert row["ok"] is False and "boom" in row["error"]


def test_model_request_budget_enforced(conn, run):
    settings = Settings(max_model_requests=1)
    mesh = FakeMesh(queries=["bad", queries("never reached")])
    with pytest.raises(BudgetExceeded):
        _call(conn, run, mesh, settings)  # attempt 1 invalid; the retry would be request 2
    assert len(mesh.calls) == 1
