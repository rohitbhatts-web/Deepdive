"""The opt-in compact drafting profile for a stored-evidence retry: it only lowers limits, is previewed offline, is never applied automatically, and still
leaves every subquestion with evidence. Scripted fixtures only; nothing here calls a model."""

import dataclasses

import pytest

from backend import db, retry
from backend.config import Settings
from tests.seed import seed_collected_run


def test_the_profile_only_lowers_limits_and_changes_no_timeout_model_or_token_limit():
    base = Settings(_env_file=None, mesh_api_key="")
    compact = retry.retry_settings(base, "compact")
    assert (compact.max_claims, compact.draft_input_chars, compact.draft_passages_per_subquestion, compact.draft_context_passages) == (5, 5000, 2, 0)
    assert compact.max_search_queries == 0 and compact.max_page_fetches == 0  # retrieval stays off
    for field in ("mesh_timeout", "max_output_tokens", "max_total_output_tokens", "max_model_requests", "max_elapsed_seconds", "drafter_model", "verifier_model"):
        assert getattr(compact, field) == getattr(base, field), field
    lower = Settings(_env_file=None, mesh_api_key="", max_claims=3, draft_input_chars=2000)  # an already lower configuration is never raised
    assert retry.retry_settings(lower, "compact").max_claims == 3 and retry.retry_settings(lower, "compact").draft_input_chars == 2000
    assert retry.retry_settings(base).max_claims == base.max_claims  # no profile named: no drafting limit is touched
    with pytest.raises(ValueError, match="unknown drafting profile"):
        retry.retry_settings(base, "turbo")


def test_the_preview_is_offline_and_every_subquestion_with_a_relevant_passage_keeps_evidence_under_the_profile(conn, settings):
    r = seed_collected_run(conn, n=3)
    plan = retry.plan(conn, r["run"], settings, "compact")
    d = plan["drafting"]
    assert d["profile"] == "compact" and d["with_profile"]["limits"]["draft_input_chars"] == 5000
    assert d["with_profile"]["characters"] <= 5000 <= d["configured"]["limits"]["draft_input_chars"]
    assert d["with_profile"]["subquestions_with_passages"] == d["configured"]["subquestions_with_passages"] > 0  # no subject loses its evidence
    assert all(v["passages"] >= 1 for v in d["with_profile"]["per_subquestion"].values())
    assert retry.plan(conn, r["run"], settings)["drafting"]["with_profile"] is None  # without a profile only the configured limits are shown


def test_the_api_applies_the_profile_only_when_asked_and_records_it(client, complete_env, monkeypatch):
    from backend.api import completion

    conn = db.connect(client.db_path)
    r = seed_collected_run(conn)
    with db.tx(conn):
        db.set_verify_status(conn, r["run"], "failed", "drafter failed")
        db.set_complete_status(conn, r["run"], "failed", "drafter failed")
    conn.close()
    complete_env(None, None)
    seen = []
    monkeypatch.setattr(completion, "execute_completion", lambda run_id, svc, verify_first=False: seen.append(svc.settings))
    assert client.get(f"/runs/{r['run']}/retry?profile=turbo").status_code == 400
    assert client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": True, "profile": "turbo"}).status_code == 400
    assert client.get(f"/runs/{r['run']}/retry?profile=compact").json()["drafting"]["with_profile"]["limits"]["max_claims"] == 5
    assert client.post(f"/runs/{r['run']}/retry", json={"confirm_paid_retry": True, "profile": "compact"}).status_code == 202
    assert seen and seen[0].max_claims == 5 and seen[0].draft_input_chars == 5000 and seen[0].max_search_queries == 0
    conn = db.connect(client.db_path)
    started = [e for e in db.list_events(conn, r["run"]) if e["event_type"] == "job_retry_started"]
    assert started[-1]["payload"]["drafting_profile"] == "compact"
    conn.close()
    assert dataclasses.is_dataclass(seen[0]) is False  # a Settings object, not services: only settings were lowered


def test_the_first_draft_selection_gives_every_subquestion_a_passage_before_any_gets_a_second(monkeypatch):
    """Attempts 3 and 4: subquestions were served in order from one character cap, so a later subquestion (HTTP/3's transport layer) got nothing."""
    from backend.agents import drafter

    sqs = [{"id": i, "question": f"question about topic{i} transport layer", "evidence_requirements": [f"topic{i} detail"]} for i in range(1, 5)]
    passages = {i: [{"id": f"S{i}-P{n}", "text": f"topic{i} detail transport layer sentence {n}. " * 30, "ordinal": n, "source_id": i} for n in range(3)] for i in range(1, 5)}
    monkeypatch.setattr(drafter.db, "list_subquestions", lambda conn, run_id: sqs)
    monkeypatch.setattr(drafter.db, "list_associations", lambda conn, run_id: [{"subquestion_id": i, "source_id": i, "passage_ids": []} for i in range(1, 5)])
    monkeypatch.setattr(drafter.db, "source_passages", lambda conn, source_id: passages[source_id])
    each = len(passages[1][0]["text"])
    tight = Settings(_env_file=None, mesh_api_key="", draft_input_chars=int(each * 5.5), draft_passages_per_subquestion=3, max_claims=8)
    offered = drafter.select_draft_passages(None, 1, tight)
    assert sorted(offered) == [1, 2, 3, 4] and all(len(v) >= 1 for v in offered.values())  # nobody is starved
    assert sum(len(p["text"]) for v in offered.values() for p in v) <= tight.draft_input_chars
    assert max(len(v) for v in offered.values()) == 2 and sum(len(v) for v in offered.values()) == 5  # four first passages, then the one spare slot became a second passage
    roomy = Settings(_env_file=None, mesh_api_key="", draft_input_chars=100000, draft_passages_per_subquestion=3)
    assert all(len(v) == 3 for v in drafter.select_draft_passages(None, 1, roomy).values())  # with room, behaviour is unchanged
