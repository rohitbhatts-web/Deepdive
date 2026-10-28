"""Regression tests for the Phase 2 quality fixes: plain queries, PDF skipping, collection statuses, source
associations, structured output, request IDs. FIXTURE Mesh/search/web (tests/fakes.py)."""

import json
from types import SimpleNamespace

import pytest

from backend import correction, db, services
from backend.agents.relevance import content_words, shortlist
from backend.agents.researcher import (
    QUERY_SYSTEM,
    SELECT_SYSTEM,
    sanitize_query,
    unsupported_reason,
)
from backend.agents.schemas import Plan
from backend.llm import parse_json_object, strict_json_schema
from tests.conftest import A, B
from tests.fakes import FakeMesh, FakeSearch, plan, queries, select
from tests.test_research_flow import start

PDF = "https://example.test/spec.pdf"
CACHE_SQ = ("How does caching work in practice here?", ["cache invalidation strategies"])
STORAGE_SQ = ("Which storage engines exist today?", ["storage engine comparison"])
PAGE_A = ("<html><head><title>Systems Guide</title></head><body>"
          "<p>Storage engine comparison: B-tree and LSM engines differ in write cost.</p>"
          "<p>Cache invalidation strategies include TTL expiry and write-through.</p></body></html>")
PAGE_B = "<html><head><title>Garden</title></head><body><p>Unrelated gardening notes about tomatoes and compost.</p></body></html>"


# --- plain-language queries --------------------------------------------------------------------------------------


@pytest.mark.parametrize("raw,clean", [
    ("sqlite vs postgres site:example.org OR site:other.org", "sqlite vs postgres"),
    ('"exact phrase" filetype:pdf official docs', "exact phrase official docs"),
    ("cache invalidation intitle:guide (strategies)", "cache invalidation strategies"),
    ("plain language question about caching", "plain language question about caching"),
])
def test_sanitize_query(raw, clean):
    assert sanitize_query(raw) == clean


def test_prompts_ask_for_plain_queries_and_primary_sources_without_naming_domains():
    assert "Do not use search operators" in QUERY_SYSTEM and "site:" in QUERY_SYSTEM  # named only as forbidden
    assert "official documentation" in QUERY_SYSTEM and "official or primary documentation" in SELECT_SYSTEM
    from backend.agents import planner, relevance, researcher

    prompts = [planner.SYSTEM, relevance.SYSTEM, QUERY_SYSTEM, SELECT_SYSTEM, str(researcher.QUERY_EXAMPLE),
               str(researcher.SELECT_EXAMPLE), str(planner.EXAMPLE), str(relevance.EXAMPLE)]
    for text in map(str.lower, prompts):
        for banned in ("sqlite", "postgres", "stackoverflow", "wikipedia", ".org", ".gov", ".edu"):
            assert banned not in text, f"a prompt hardcodes {banned!r}"


def test_operators_in_generated_queries_are_removed_before_searching_and_logged(client, research):
    search = FakeSearch({"cache invalidation guide": [(A, "A", "s")]})
    mesh = FakeMesh(planner=plan(CACHE_SQ), queries=queries("cache invalidation guide site:example.org OR site:x.org"),
                    select=select(A))
    research(mesh, search)
    run_id, _ = start(client)
    assert search.queries == ["cache invalidation guide"]
    events = client.get(f"/runs/{run_id}/events").json()
    cleaned = [e["payload"] for e in events if e["event_type"] == "query_sanitized"]
    assert cleaned == [{"subquestion_id": 1, "original": "cache invalidation guide site:example.org OR site:x.org",
                        "cleaned": "cache invalidation guide"}]
    assert client.get(f"/runs/{run_id}/searches").json()[0]["query"] == "cache invalidation guide"


# --- skipping results that cannot be ingested ----------------------------------------------------------------------


def test_unsupported_reason_uses_only_the_url():
    assert unsupported_reason("https://x.test/Doc.PDF") == "pdf_not_supported"
    assert unsupported_reason("https://x.test/doc.pdf?download=1") == "pdf_not_supported"
    assert unsupported_reason("https://x.test/pdf/guide.html") is None


def test_pdf_results_are_skipped_logged_and_not_offered_to_the_model(client, research):
    search = FakeSearch({"cache invalidation guide": [(PDF, "The spec", "s"), (A, "A", "s")]})
    mesh = FakeMesh(planner=plan(CACHE_SQ), queries=queries("cache invalidation guide"), select=select(A))
    research(mesh, search)
    run_id, _ = start(client)
    selection_prompt = next(c for c in mesh.calls if "choose the pages" in c["messages"][0]["content"])
    assert PDF not in selection_prompt["messages"][1]["content"]
    skipped = [e["payload"] for e in client.get(f"/runs/{run_id}/events").json() if e["event_type"] == "result_skipped"]
    assert skipped == [{"subquestion_id": 1, "search_id": 1, "url": PDF, "reason": "pdf_not_supported"}]
    stored_hits = client.get(f"/runs/{run_id}/searches").json()[0]["result"]["hits"]
    assert PDF in [h["url"] for h in stored_hits]  # the raw search history stays honest


def test_all_pdf_results_means_collection_incomplete_without_a_selection_call(client, research):
    mesh = FakeMesh(planner=plan(CACHE_SQ), queries=queries("cache invalidation guide"))
    research(mesh, FakeSearch({"cache invalidation guide": [(PDF, "The spec", "s")]}))
    run_id, _ = start(client)
    assert "select" not in mesh.steps()
    assert client.get(f"/runs/{run_id}/subquestions").json()[0]["status"] == "collection_incomplete"


# --- statuses describe collection, not coverage -----------------------------------------------------------------------


def test_status_vocabulary_for_collected_incomplete_and_failed(client, research, web):
    web.pages[B] = __import__("httpx").ConnectError("down")
    mesh = FakeMesh(planner=plan(CACHE_SQ, STORAGE_SQ), queries=[queries("cache invalidation guide"), queries("storage engine list")],
                    select=[select(A), select(B)])
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s")], "storage engine list": [(B, "B", "s")]}))
    run_id, _ = start(client)
    subs = client.get(f"/runs/{run_id}/subquestions").json()
    assert [s["status"] for s in subs] == ["evidence_collected", "collection_incomplete"]
    run = client.get(f"/runs/{run_id}/research").json()
    assert run["status"] == "collection_finished"  # the pipeline ended; it does not claim coverage or support
    done = [e["payload"] for e in client.get(f"/runs/{run_id}/events").json() if e["event_type"] == "subquestion_done"]
    assert done[1]["reason"] == "none of the selected pages could be collected"


def test_unresolved_subquestions_are_listed_when_the_run_stops(client, research):
    mesh = FakeMesh(planner=plan(CACHE_SQ, STORAGE_SQ), queries=queries("cache invalidation guide"), select=select(A))
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s")]}), max_model_requests=3)
    run_id, _ = start(client)
    last = client.get(f"/runs/{run_id}/events").json()[-1]["payload"]
    assert last["status"] == "stopped" and last["unresolved_subquestions"] == [2]
    assert [s["status"] for s in client.get(f"/runs/{run_id}/subquestions").json()] == [
        "evidence_collected", "collection_incomplete"]


# --- associations ------------------------------------------------------------------------------------------------------


def associations_scenario(client, research, web, assessments, **overrides):
    """Sub 1 selects pages A and B; sub 2 selects nothing. Only page A mentions sub 2's requirements."""
    web.html(A, PAGE_A)
    web.html(B, PAGE_B)
    mesh = FakeMesh(planner=plan(CACHE_SQ, STORAGE_SQ),
                    queries=[queries("cache invalidation guide"), queries("storage engine list")],
                    select=[select(A, B), select()], relevance=assessments)
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s"), (B, "B", "s")],
                               "storage engine list": [(A, "A", "s")]}), **overrides)
    run_id, _ = start(client)
    return run_id, mesh


def test_relevant_source_is_linked_to_a_second_subquestion_with_passages_and_marked_unverified(client, research, web):
    run_id, _ = associations_scenario(client, research, web, {"assessments": [
        {"candidate": "c1", "relevant": True, "passage_ids": ["S1-P0"], "explanation": "compares B-tree and LSM engines"}]})
    assoc = client.get(f"/runs/{run_id}/associations").json()
    assert [(a["subquestion_id"], a["source_id"], a["origin"], a["verification"]) for a in assoc] == [
        (1, 1, "selected_from_search", "unverified"), (1, 2, "selected_from_search", "unverified"),
        (2, 1, "relevance_assessment", "unverified")]
    extra = assoc[2]
    assert extra["passage_ids"] == ["S1-P0"] and extra["explanation"] == "compares B-tree and LSM engines"
    assert not any(a["subquestion_id"] == 2 and a["source_id"] == 2 for a in assoc)  # not linked to everything
    assert [s["status"] for s in client.get(f"/runs/{run_id}/subquestions").json()] == [
        "evidence_collected", "evidence_collected"]  # upgraded by the association
    # the passage ID cited really exists and belongs to that source
    cited = [p["id"] for p in client.get(f"/runs/{run_id}/passages?source_id=1").json()]
    assert extra["passage_ids"][0] in cited


def test_only_overlapping_pairs_are_offered_and_the_check_counts_toward_the_request_cap(client, research, web):
    run_id, mesh = associations_scenario(client, research, web, {"assessments": []})
    prompt = next(c for c in mesh.calls if "running a relevance check" in c["messages"][0]["content"])
    user = prompt["messages"][1]["content"]
    assert 'id="c1"' in user and 'id="c2"' not in user  # (sub 2, page B) shares no words, so it is not offered
    assert "tomatoes" not in user
    assert client.get(f"/runs/{run_id}/research").json()["model_usage"]["model_calls"] == 6  # 5 + relevance
    calls = client.get(f"/runs/{run_id}/model-calls").json()
    assert [c["agent"] for c in calls][-1] == "relevance"


def test_association_call_respects_the_model_request_cap(client, research, web):
    run_id, mesh = associations_scenario(client, research, web, {"assessments": []}, max_model_requests=5)
    status = client.get(f"/runs/{run_id}/research").json()
    assert (status["status"], status["stop_reason"]) == ("stopped", "max_model_requests reached (5)")
    assert "relevance" not in mesh.steps()


@pytest.mark.parametrize("assessments,event", [
    ([{"candidate": "c1", "relevant": True, "passage_ids": ["S9-P9"], "explanation": "x"}], "assessment_rejected"),
    ([{"candidate": "c7", "relevant": True, "passage_ids": ["S1-P0"], "explanation": "x"}], "assessment_rejected"),
    ([{"candidate": "c1", "relevant": False, "passage_ids": [], "explanation": "only shares words"}], "association_declined"),
])
def test_invalid_or_negative_assessments_create_no_association(client, research, web, assessments, event):
    run_id, _ = associations_scenario(client, research, web, {"assessments": assessments})
    assert [a["origin"] for a in client.get(f"/runs/{run_id}/associations").json()] == ["selected_from_search"] * 2
    events = [e["event_type"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert event in events
    assert client.get(f"/runs/{run_id}/subquestions").json()[1]["status"] == "collection_incomplete"


def test_no_model_call_when_nothing_overlaps(client, research, web):
    web.html(A, PAGE_B)
    mesh = FakeMesh(planner=plan(CACHE_SQ, STORAGE_SQ), queries=[queries("cache invalidation guide"), queries("storage engine list")],
                    select=[select(A), select()])
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s")], "storage engine list": [(A, "A", "s")]}))
    run_id, _ = start(client)
    assert "relevance" not in mesh.steps()
    reasons = [e["payload"]["reason"] for e in client.get(f"/runs/{run_id}/events").json() if e["event_type"] == "association_skipped"]
    assert reasons and "overlapping" in reasons[0]


def test_shortlist_uses_content_words_and_a_minimum_overlap():
    sq = {"question": "Which storage engines exist today?", "evidence_requirements": ["storage engine comparison"]}
    passages = [{"id": "S1-P0", "ordinal": 0, "text": "Storage engine comparison of LSM and B-tree."},
                {"id": "S1-P1", "ordinal": 1, "text": "Tomatoes need sun."},
                {"id": "S1-P2", "ordinal": 2, "text": "The storage was empty."}]
    assert [p["id"] for _, p in shortlist(sq, passages, top=2, min_overlap=2)] == ["S1-P0"]
    words = content_words("Which storage engines")
    assert "which" not in words and correction._stem("storage") in words  # stems: "engines" and "engine" now count as the same word
    assert content_words("merging developers") == content_words("merge developer")


# --- structured output, request IDs, parsing ------------------------------------------------------------------------------


def test_json_schema_is_inlined_closed_and_free_of_validation_keywords():
    schema = strict_json_schema(Plan)
    text = json.dumps(schema)
    for banned in ("$ref", "$defs", "minLength", "maxLength", "minItems", "maxItems", "title", "default"):
        assert banned not in text
    item = schema["properties"]["subquestions"]["items"]
    assert item["type"] == "object" and item["additionalProperties"] is False
    assert item["required"] == ["question", "evidence_requirements", "rationale", "covers"]
    assert schema["additionalProperties"] is False and schema["required"] == ["asks", "subquestions"]


def test_response_format_is_sent_only_to_models_flagged_as_structured(client, research):
    mesh = FakeMesh(planner=plan(CACHE_SQ), queries=queries("cache invalidation guide"), select=select(A))
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s")]}), structured=frozenset({"fixture-researcher"}))
    run_id, _ = start(client)
    by_model = {c["model"]: c for c in mesh.calls}
    assert "response_format" not in by_model["fixture-planner"]  # not flagged, so the prompt-only path
    rf = by_model["fixture-researcher"]["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["name"] in ("QueryPlan", "UrlSelection")
    assert rf["json_schema"]["schema"]["additionalProperties"] is False
    started = next(e for e in client.get(f"/runs/{run_id}/events").json() if e["event_type"] == "research_started")
    assert started["payload"]["structured_output_models"] == ["fixture-researcher"]


def test_build_services_flags_structured_models_from_the_catalog(monkeypatch, settings):
    rows = [{"id": "fixture-planner", "supports_structured_output": False, "model_type": "text"},
            {"id": "fixture-researcher", "supports_structured_output": True, "model_type": "text"}]
    monkeypatch.setattr(services, "get_mesh_client", lambda: SimpleNamespace(get=lambda path, cast_to: rows))
    s = settings.model_copy(update={"mesh_api_key": "k-123456", "tavily_api_key": "t-123456"})
    assert services.build_services(s).llm.structured_models == frozenset({"fixture-researcher"})


def test_parser_takes_the_first_object_and_ignores_trailing_text():
    assert parse_json_object('{"a": 1}\nHope this helps!') == {"a": 1}
    assert parse_json_object('{"a": 1}{"b": 2}') == {"a": 1}
    assert parse_json_object('Here you go: ```json\n{"a": {"b": [1, 2]}}\n```') == {"a": {"b": [1, 2]}}
    with pytest.raises(ValueError):
        parse_json_object("[1, 2]")


def test_prompts_carry_an_example_and_require_a_single_json_object(client, research):
    mesh = FakeMesh(planner=plan(CACHE_SQ), queries=queries("cache invalidation guide"), select=select(A))
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s")]}))
    start(client)
    for call in mesh.calls:
        system = call["messages"][0]["content"]
        assert "exactly one JSON object" in system and "Example of the exact shape" in system
        assert "$ref" not in system and "minLength" not in system  # compact schema, not Pydantic's verbose one


def test_request_id_header_is_stored_per_model_call_and_exposed(client, research):
    mesh = FakeMesh(planner=plan(CACHE_SQ), queries=queries("cache invalidation guide"), select=select(A))
    research(mesh, FakeSearch({"cache invalidation guide": [(A, "A", "s")]}))
    run_id, _ = start(client)
    calls = client.get(f"/runs/{run_id}/model-calls").json()
    assert [c["request_id"] for c in calls][:3] == ["req_fixture_1", "req_fixture_2", "req_fixture_3"]


def test_schema_v2_database_is_upgraded_without_losing_rows(tmp_path):
    path = tmp_path / "v2.db"
    c = db.connect(path)
    run = db.create_run(c, "Survives?")
    db.record_model_call(c, run["id"], agent="planner", model="m", attempt=1, ok=True, request_id="req_1")
    c.execute("ALTER TABLE model_calls DROP COLUMN request_id")
    c.execute("DROP TABLE source_associations")
    c.execute("PRAGMA user_version = 2")
    c.close()
    c = db.connect(path)
    assert {r[1] for r in c.execute("PRAGMA table_info(model_calls)")} >= {"request_id"}
    assert c.execute("SELECT count(*) FROM source_associations").fetchone()[0] == 0
    assert db.list_model_calls(c, run["id"])[0]["request_id"] is None  # old row kept, no invented ID
    c.close()
