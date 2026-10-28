"""Usage reporting: unknown token totals stay unknown (NULL, never 0), a reported zero stays 0, and a failed request keeps the provider's request id when the
exception carries one in a structured field. Deterministic: SQLite and locally built SDK exceptions only; no network, no model call."""

import httpx
import openai
import pytest
from pydantic import BaseModel

from backend import db
from backend.budget import Budget
from backend.llm import LLM, ModelCallError, exception_request_id


class Answer(BaseModel):
    ok: str


def call(conn, run_id, ok=True, p=None, c=None, t=None):
    db.record_model_call(conn, run_id, agent="planner", model="m", attempt=1, ok=ok, latency_ms=5, prompt_tokens=p, completion_tokens=c, total_tokens=t)


# ---------------------------------------------------------------- totals


def test_no_calls_unknown_usage_partial_usage_and_a_reported_zero_are_four_different_answers(conn):
    none_run = db.create_run(conn, "no calls")["id"]
    unknown_run = db.create_run(conn, "all unknown")["id"]
    partial_run = db.create_run(conn, "partial")["id"]
    zero_run = db.create_run(conn, "reported zero")["id"]
    call(conn, unknown_run, ok=False)
    call(conn, partial_run, p=667, c=379, t=1046)
    call(conn, partial_run, p=710, c=247, t=957)
    call(conn, partial_run, ok=False)
    call(conn, zero_run, p=0, c=0, t=0)
    assert db.model_usage_totals(conn, none_run) == {"model_calls": 0, "failed_calls": 0, "calls_with_usage": 0, "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    assert db.model_usage_totals(conn, unknown_run) == {"model_calls": 1, "failed_calls": 1, "calls_with_usage": 0, "prompt_tokens": None, "completion_tokens": None, "total_tokens": None}
    assert db.model_usage_totals(conn, partial_run) == {"model_calls": 3, "failed_calls": 1, "calls_with_usage": 2, "prompt_tokens": 1377, "completion_tokens": 626, "total_tokens": 2003}
    assert db.model_usage_totals(conn, zero_run) == {"model_calls": 1, "failed_calls": 0, "calls_with_usage": 1, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_the_research_endpoint_reports_unknown_totals_as_null(client):
    run_id = client.post("/runs", json={"question": "How do Python lists and tuples differ?"}).json()["id"]
    conn = db.connect(client.db_path)
    call(conn, run_id, ok=False)
    conn.close()
    usage = client.get(f"/runs/{run_id}/research").json()["model_usage"]
    assert usage["model_calls"] == 1 and usage["calls_with_usage"] == 0
    assert usage["prompt_tokens"] is None and usage["completion_tokens"] is None and usage["total_tokens"] is None


def test_per_agent_verification_usage_is_null_when_no_call_of_the_agent_reported_it(client):
    run_id = client.post("/runs", json={"question": "How do Python lists and tuples differ?"}).json()["id"]
    conn = db.connect(client.db_path)
    db.record_model_call(conn, run_id, agent="drafter", model="m", attempt=1, ok=False, latency_ms=5)
    db.record_model_call(conn, run_id, agent="verifier", model="m", attempt=1, ok=True, latency_ms=5, prompt_tokens=0, completion_tokens=0, total_tokens=0)
    conn.close()
    by_agent = client.get(f"/runs/{run_id}/verification").json()["model_usage_by_agent"]
    assert by_agent["drafter"]["prompt_tokens"] is None and by_agent["drafter"]["completion_tokens"] is None
    assert by_agent["verifier"]["prompt_tokens"] == 0 and by_agent["verifier"]["completion_tokens"] == 0  # a reported zero is still a zero


# ---------------------------------------------------------------- request id of a failed call


def status_error(headers, body, status=503):
    request = httpx.Request("POST", "http://mesh.invalid/v1/chat/completions")
    response = httpx.Response(status, headers=headers, json=body, request=request)
    return openai.InternalServerError("Error code: 503", response=response, body=body.get("error"))


UPSTREAM = {"error": {"code": "upstream_error", "message": "The model provider is temporarily unavailable.", "provider_code": "internal_provider_error"}}


def test_the_request_id_comes_from_a_structured_field_and_is_never_invented():
    assert exception_request_id(status_error({"x-request-id": "req_HEADER"}, {**UPSTREAM, "request_id": "req_BODY"})) == "req_HEADER"  # the SDK's own field wins
    assert exception_request_id(status_error({}, {**UPSTREAM, "request_id": "req_BODY"})) == "req_BODY"  # else the top level of the provider's JSON error body
    assert exception_request_id(status_error({}, UPSTREAM)) is None  # neither: unknown
    assert exception_request_id(TimeoutError("wall-clock deadline of 60s reached")) is None  # never connected, so no id exists
    # an id that only appears inside the message text is not a structured field, so it is not used
    assert exception_request_id(RuntimeError("Error code: 503 - {'request_id': 'req_IN_TEXT'}")) is None
    assert exception_request_id(status_error({}, {**UPSTREAM, "request_id": ["not", "a", "string"]})) is None
    assert exception_request_id(status_error({}, {**UPSTREAM, "request_id": "   "})) is None


class RaisingCompletions:
    def __init__(self, exc):
        self.exc, self.calls = exc, 0

    def create(self, **_kw):
        self.calls += 1
        raise self.exc


class RaisingClient:
    def __init__(self, exc):
        self.chat = type("Chat", (), {})()
        self.completions = RaisingCompletions(exc)
        self.chat.completions = type("C", (), {"with_raw_response": self.completions})()


@pytest.mark.parametrize("exc, expected", [
    (status_error({}, {**UPSTREAM, "request_id": "req_FIXTURE_UPSTREAM_503"}), "req_FIXTURE_UPSTREAM_503"),
    (status_error({"x-request-id": "req_HEADER"}, UPSTREAM), "req_HEADER"),
    (TimeoutError("wall-clock deadline of 60s reached"), None),
])
def test_a_failed_call_records_its_request_id_and_nothing_else_about_it_changes(conn, settings, exc, expected):
    run_id = db.create_run(conn, "q")["id"]
    client = RaisingClient(exc)
    llm, budget = LLM(client, settings), Budget(settings)
    with pytest.raises(ModelCallError):
        llm.call(conn, run_id, budget, agent="researcher", model="m", system="s", user="u", schema=Answer)
    row = conn.execute("SELECT * FROM model_calls WHERE run_id = ?", (run_id,)).fetchall()
    assert len(row) == 1 and client.completions.calls == 1  # one request, no retry
    r = row[0]
    assert r["ok"] == 0 and r["request_id"] == expected and r["attempt"] == 1
    assert r["prompt_tokens"] is None and r["completion_tokens"] is None and r["total_tokens"] is None  # usage stays unknown
    assert budget.snapshot()["model_requests"][0] == 1  # charged exactly as before
    assert budget.snapshot()["output_tokens"][0] == settings.max_output_tokens  # the cap still counts the per-call maximum for a failed call
