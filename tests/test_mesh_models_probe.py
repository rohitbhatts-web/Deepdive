"""The /models reachability probe, tested offline before any network request: a bare-list body (as Mesh returns it), the {"data": [...]} shape, missing models,
malformed bodies, error statuses with a request id, a connection failure, and that no credential reaches the output. httpx.MockTransport only; no network."""

import sys
from pathlib import Path

import httpx
import pytest
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import mesh_models_probe as mp

SECRET = "TESTVALUE-not-a-real-key-123"
BARE = [
    {"id": "qwen/qwen3-235b-a22b-instruct-2507", "model_type": "text", "supports_structured_output": True, "supports_system_prompt": True, "price": {"in": 1}},
    {"id": "anthropic/claude-haiku-4.5", "model_type": "text", "supports_structured_output": True},
    {"id": "other/model", "model_type": "image"},
    {"no_id": True},
    "not an object",
]
WANTED = {"qwen/qwen3-235b-a22b-instruct-2507", "anthropic/claude-haiku-4.5", "missing/model"}


def client_for(handler) -> OpenAI:
    return OpenAI(api_key=SECRET, base_url="http://mesh.invalid/v1", max_retries=0, http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_a_bare_list_and_the_data_shape_are_both_read_and_the_flags_are_whitelisted():
    for body in (BARE, {"object": "list", "data": BARE}):
        s = mp.summarize_catalog(body, WANTED)
        assert s["models_listed"] == 3 and s["entries_without_id"] == 2  # an entry without an id and one that is not an object are both counted
        assert s["configured"] == {"anthropic/claude-haiku-4.5": True, "missing/model": False, "qwen/qwen3-235b-a22b-instruct-2507": True}
        assert s["configured_flags"]["qwen/qwen3-235b-a22b-instruct-2507"] == {"model_type": "text", "supports_structured_output": True, "supports_system_prompt": True}  # no price field


@pytest.mark.parametrize("body", [{"error": "x"}, "text", 5, None])
def test_a_body_that_is_not_a_model_list_is_an_error_not_an_empty_catalog(body):
    with pytest.raises(ValueError):
        mp.summarize_catalog(body, WANTED)


def test_one_request_captures_status_timing_request_id_and_the_configured_models():
    seen = []

    def handler(req):
        seen.append(req)
        return httpx.Response(200, json=BARE, headers={"x-request-id": "req_MODELS_OK"})

    r = mp.probe(client_for(handler), WANTED)
    assert len(seen) == 1 and seen[0].url.path == "/v1/models" and seen[0].method == "GET"  # exactly one read-only request
    assert r["status"] == 200 and r["request_id"] == "req_MODELS_OK" and r["error"] is None
    assert isinstance(r["wall_ms"], int) and "httpx_elapsed_ms" not in r  # one wall time only; no component timings are claimed
    assert r["configured"]["missing/model"] is False and r["configured"]["anthropic/claude-haiku-4.5"] is True


def test_an_error_status_is_the_result_with_its_status_and_request_id():
    r = mp.probe(client_for(lambda req: httpx.Response(503, json={"error": {"message": "down"}}, headers={"x-request-id": "req_503"})), WANTED)
    assert r["status"] == 503 and r["request_id"] == "req_503" and r["error"] == "InternalServerError"
    r = mp.probe(client_for(lambda req: httpx.Response(401, json={"error": {"message": "bad key"}})), WANTED)
    assert r["status"] == 401 and r["request_id"] is None  # no id was sent, so none is shown


def test_a_connection_failure_has_no_status_and_still_reports_the_measured_time():
    def handler(req):
        raise httpx.ConnectError("refused", request=req)

    r = mp.probe(client_for(handler), WANTED)
    assert r["status"] is None and r["request_id"] is None and r["error"] == "APIConnectionError" and isinstance(r["wall_ms"], int)


def test_a_200_with_a_malformed_body_keeps_the_status_and_names_the_failure():
    r = mp.probe(client_for(lambda req: httpx.Response(200, json={"unexpected": 1}, headers={"x-request-id": "req_BAD_BODY"})), WANTED)
    assert r["status"] == 200 and r["request_id"] == "req_BAD_BODY" and r["error"] == "ValueError" and "models_listed" not in r


def test_no_credential_reaches_the_printed_output():
    seen_auth = []

    def handler(req):
        seen_auth.append(req.headers.get("authorization"))
        return httpx.Response(200, json=BARE)

    text = mp.render("api.example.test", mp.probe(client_for(handler), WANTED))
    assert seen_auth == [f"Bearer {SECRET}"]  # the key is sent (that is the point) ...
    assert SECRET not in text and "Bearer" not in text and "authorization" not in text.lower()  # ... and never printed
