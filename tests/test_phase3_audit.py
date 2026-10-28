"""Phase 3 audit tests (docs/PHASE3_REVIEW.md): eligibility on every path, evidence boundaries, attribution handling,
outcome handling, integrity-vs-semantic separation. Tests with scripted verifier replies establish how the pipeline
HANDLES a verdict; they say nothing about whether a real model would give it."""

import sqlite3

import pytest

from backend import claims, db
from backend.agents import drafter, verifier
from backend.agents.schemas import VerifierVerdict
from backend.budget import Budget
from backend.llm import LLM, strict_json_schema
from tests.fakes import FakeMesh, verdict
from tests.seed import PARAS, Q0, Q1, Q2, seed_collected_run

NEIGHBOUR_NOTE = "S1-P0 (context) says the same thing"


def add(conn, r, text, ref, quote, sq="sq1", **kw):
    with db.tx(conn):
        return claims.add_claim(conn, r["run"], r[sq], text, [(ref, quote)], origin=kw.pop("origin", "drafted"), **kw)


def judged(conn, settings, cid, semantic=None, passages=(), integrity=True):
    if integrity:
        claims.check_integrity(conn, cid, settings)
    if semantic:
        with db.tx(conn):
            claims.record_result(conn, cid, "semantic", semantic, "fixture", model="m", passages=list(passages),
                                 details={"rubric": claims.CURRENT_RUBRIC})


# --- eligibility: every path --------------------------------------------------------------------------------------------


@pytest.fixture
def mixed(conn, settings):
    r = seed_collected_run(conn)
    ids = {}
    ids["good"] = add(conn, r, Q0, r["p"](0), Q0)
    judged(conn, settings, ids["good"], "supported", [(r["p"](0), "claim_evidence")])
    ids["test_input"] = claims.add_challenge_claim(conn, r["run"], r["sq1"], Q0, [(r["p"](0), Q0)],
                                                   alteration="control", derived_from=r["p"](0))
    judged(conn, settings, ids["test_input"], "supported", [(r["p"](0), "claim_evidence")])
    ids["integrity_failed"] = add(conn, r, Q0, "S9-P9", Q0)
    judged(conn, settings, ids["integrity_failed"], "supported")  # a stray 'supported' row must not matter
    for name, verdict_ in (("error", "error"), ("partial", "partially_supported"), ("unsupported", "unsupported"),
                           ("contradicted", "contradicted")):
        ids[name] = add(conn, r, Q0, r["p"](0), Q0)
        judged(conn, settings, ids[name], verdict_)
    ids["unjudged"] = add(conn, r, Q0, r["p"](0), Q0)
    claims.check_integrity(conn, ids["unjudged"], settings)
    ids["stale_v1"] = add(conn, r, Q0, r["p"](0), Q0)
    judged(conn, settings, ids["stale_v1"], "supported", [(r["p"](0), "claim_evidence")])
    ids["stale_v2"] = add(conn, r, "SQLite runs as a library.", r["p"](0), Q0, parent_claim_id=ids["stale_v1"], version=2)
    claims.check_integrity(conn, ids["stale_v2"], settings)
    return r, ids


def test_only_the_natural_current_supported_claim_is_eligible_by_every_function(conn, mixed):
    r, ids = mixed
    flags = {name: claims.get_claim(conn, r["run"], cid)["eligible_for_report"] for name, cid in ids.items()}
    assert [n for n, ok in flags.items() if ok] == ["good"]
    assert [c["id"] for c in claims.eligible_claims(conn, r["run"])] == [ids["good"]]
    assert [c["id"] for c in claims.list_claims(conn, r["run"]) if c["eligible_for_report"]] == [ids["good"]]
    assert claims.summary(conn, r["run"])["eligible_for_report"] == 1
    stale = claims.get_claim(conn, r["run"], ids["stale_v1"])
    assert stale["semantic_verdict"] == "supported" and stale["superseded_by"] == ids["stale_v2"]  # supported, but stale


def test_every_api_path_agrees_with_the_single_eligibility_rule(client, verify_env):
    conn = db.connect(client.db_path)
    r, ids = seed_collected_run(conn), {}
    from tests.conftest import Settings

    s = Settings(mesh_api_key="")
    ids["good"] = add(conn, r, Q0, r["p"](0), Q0)
    judged(conn, s, ids["good"], "supported", [(r["p"](0), "claim_evidence")])
    ids["test_input"] = claims.add_challenge_claim(conn, r["run"], r["sq1"], Q0, [(r["p"](0), Q0)],
                                                   alteration="control", derived_from=r["p"](0))
    judged(conn, s, ids["test_input"], "supported", [(r["p"](0), "claim_evidence")])
    ids["v1"] = add(conn, r, Q0, r["p"](0), Q0)
    judged(conn, s, ids["v1"], "supported", [(r["p"](0), "claim_evidence")])
    add(conn, r, "SQLite runs as a library.", r["p"](0), Q0, parent_claim_id=ids["v1"], version=2)
    conn.close()
    run = r["run"]
    listed = {c["id"]: c for c in client.get(f"/runs/{run}/claims").json()}
    assert [i for i, c in listed.items() if c["eligible_for_report"]] == [ids["good"]]
    assert [c["id"] for c in client.get(f"/runs/{run}/claims?eligible_only=true").json()] == [ids["good"]]
    assert [c["id"] for c in client.get(f"/runs/{run}/claims?status=supported&eligible_only=true").json()] == [ids["good"]]
    for cid, claim in listed.items():
        assert client.get(f"/runs/{run}/claims/{cid}").json()["eligible_for_report"] == claim["eligible_for_report"]
    v = client.get(f"/runs/{run}/verification").json()
    assert v["eligible_for_report"] == 1 and v["by_semantic"]["supported"] == 3  # three 'supported' verdicts, one eligible
    assert listed[ids["v1"]]["superseded_by"] is not None and listed[ids["test_input"]]["origin"] == "controlled_challenge"


# --- evidence boundaries -----------------------------------------------------------------------------------------------------


def run_verifier(conn, settings, cid, reply):
    mesh = FakeMesh(verify=reply)
    result = verifier.verify_claim(conn, LLM(mesh, settings), Budget(settings), settings, cid)
    return result, mesh


def test_a_neighbour_the_verdict_relied_on_is_recorded_as_supporting_evidence(conn, settings):
    r = seed_collected_run(conn)
    cid = add(conn, r, "Version 3.45 was released in January 2024.", r["p"](1), Q1)
    claims.check_integrity(conn, cid, settings)
    result, mesh = run_verifier(conn, settings, cid, verdict(
        "supported", used=[r["p"](1), r["p"](0)], explanation="S1-P1 gives the date; S1-P0 names the product"))
    assert "<context passage_id=\"S1-P0\">" in mesh.calls[0]["messages"][1]["content"]
    assert {(p["passage_id"], p["role"]) for p in result["passages"]} == {(r["p"](1), "claim_evidence"), (r["p"](0), "neighbour_context")}
    claim = claims.get_claim(conn, r["run"], cid)
    assert claim["eligible_for_report"] is True
    cites = {c["passage_id"]: c for c in claim["citations"]}
    assert cites[r["p"](1)]["quotes"] == [Q1] and cites[r["p"](0)]["role"] == "neighbour_context" and cites[r["p"](0)]["quotes"] == []
    assert result["evidence_ids"] == [e["id"] for e in claim["evidence"]]  # only the claim's own row, not the neighbour


def test_support_that_rests_only_on_a_neighbour_is_not_full_support(conn, settings):
    r = seed_collected_run(conn)
    cid = add(conn, r, "Version 3.45 was released in January 2024.", r["p"](1), Q1)
    claims.check_integrity(conn, cid, settings)
    result, _ = run_verifier(conn, settings, cid, verdict("supported", used=[r["p"](0)]))
    assert result["verdict"] == "partially_supported" and result["details"]["support_only_in_context"] is True
    assert "context passage" in result["reason"] and result["evidence_ids"] == []
    assert [p["role"] for p in result["passages"]] == ["neighbour_context"]
    claim = claims.get_claim(conn, r["run"], cid)
    assert claim["eligible_for_report"] is False and [c["role"] for c in claim["citations"]] == ["neighbour_context"]


def test_two_quotes_from_one_passage_are_both_linked(conn, settings):
    r = seed_collected_run(conn)
    with db.tx(conn):
        cid = claims.add_claim(conn, r["run"], r["sq1"], "SQLite is embedded in the application and stores data in one file.",
                               [(r["p"](0), "SQLite runs as a library inside the process of the calling program."),
                                (r["p"](0), "Each database is kept in one ordinary file.")], origin="drafted")
    claims.check_integrity(conn, cid, settings)
    result, _ = run_verifier(conn, settings, cid, verdict("supported", used=[r["p"](0)]))
    assert result["evidence_ids"] == [e["id"] for e in claims.claim_evidence(conn, cid)] and len(result["evidence_ids"]) == 2
    assert claims.get_claim(conn, r["run"], cid)["citations"][0]["quotes"][0].startswith("SQLite runs as a library")


def test_a_verdict_cannot_use_another_runs_passage_and_result_passages_are_immutable(conn, settings):
    r, other = seed_collected_run(conn), seed_collected_run(conn, n=2)
    cid = add(conn, r, Q0, r["p"](0), Q0)
    claims.check_integrity(conn, cid, settings)
    with pytest.raises(sqlite3.IntegrityError, match="own run"), db.tx(conn):
        claims.record_result(conn, cid, "semantic", "supported", "x", passages=[(other["p"](0), "claim_evidence")])
    with db.tx(conn):
        claims.record_result(conn, cid, "semantic", "supported", "x", passages=[(r["p"](0), "claim_evidence")])
    for sql in ("UPDATE verification_result_passages SET role = 'neighbour_context'", "DELETE FROM verification_result_passages"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable|never deleted"):
            conn.execute(sql)


def test_citations_exist_only_after_a_semantic_verdict(conn, settings):
    r = seed_collected_run(conn)
    failed = add(conn, r, Q0, "S9-P9", Q0)
    claims.check_integrity(conn, failed, settings)
    fresh = add(conn, r, Q0, r["p"](0), Q0)
    claims.check_integrity(conn, fresh, settings)
    assert claims.get_claim(conn, r["run"], failed)["citations"] == []
    assert claims.get_claim(conn, r["run"], fresh)["citations"] == []


# --- attribution and atomicity -------------------------------------------------------------------------------------------------


def test_drafter_prompt_separates_descriptions_from_recommendations_and_requires_splitting():
    text = drafter.SYSTEM
    assert "ALWAYS attribute them to the page" in text and "Never restate a recommendation as plain fact" in text
    assert "is two claims" in text and "One claim = one independently checkable assertion" in text
    assert "state them plainly" in text  # descriptions are not forced into attribution


def test_verifier_prompt_checks_attribution_without_penalising_correct_attribution():
    text = verifier.SYSTEM
    assert "Attribution:" in text and "attribution mismatch" in text
    assert "do not penalise correct attribution" in text and "do not treat every recommendation as unsupported" in text
    assert "judge each one" in text  # compound claims
    for banned in ("sqlite", "postgres", "wikipedia", ".org"):
        assert banned not in (text + drafter.SYSTEM).lower()


def test_attribution_is_a_required_closed_field_of_the_verifier_schema():
    schema = strict_json_schema(VerifierVerdict)
    aspects = schema["properties"]["aspects"]
    assert "attribution" in aspects["properties"] and "attribution" in aspects["required"]
    assert set(aspects["properties"]["attribution"]["enum"]) == {"ok", "not_applicable", "mismatch", "missing"}


def test_scripted_attribution_handling_downgrades_unattributed_opinion_but_not_attributed_claims(conn, settings):
    r = seed_collected_run(conn)
    quote = PARAS[1].split(". ")[0] + "."
    unattributed = add(conn, r, "Most deployments will use cloud storage by 2030.", r["p"](1), quote)
    attributed = add(conn, r, "The maintainers expect that most deployments will use cloud storage by 2030.", r["p"](1), quote)
    claims.check_integrity(conn, unattributed, settings)
    claims.check_integrity(conn, attributed, settings)
    bad, _ = run_verifier(conn, settings, unattributed, verdict("supported", used=[r["p"](1)], attribution="mismatch"))
    good, _ = run_verifier(conn, settings, attributed, verdict("supported", used=[r["p"](1)], attribution="ok"))
    assert bad["verdict"] == "partially_supported" and "attribution=mismatch" in bad["reason"]
    assert bad["details"]["model_verdict"] == "supported" and bad["details"]["aspects"]["attribution"] == "mismatch"
    assert good["verdict"] == "supported"  # accurate attribution is not penalised
    assert claims.get_claim(conn, r["run"], attributed)["eligible_for_report"] is True


# --- outcome handling -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("value", ["partially_supported", "contradicted", "supported"])
def test_a_verdict_that_rests_on_evidence_but_cites_none_is_an_error(conn, settings, value):
    r = seed_collected_run(conn)
    cid = add(conn, r, Q0, r["p"](0), Q0)
    claims.check_integrity(conn, cid, settings)
    result, _ = run_verifier(conn, settings, cid, verdict(value, used=["S99-P99"]))
    assert result["verdict"] == "error" and result["details"]["model_verdict"] == value
    assert claims.get_claim(conn, r["run"], cid)["eligible_for_report"] is False


def test_unsupported_may_cite_nothing_and_stays_unsupported_not_error(conn, settings):
    """Found by the live recheck: an unrelated claim was judged 'unsupported' (no passage establishes it) but was
    stored as 'error' because it cited no evidence. 'unsupported' is a real verdict with nothing to cite."""
    r = seed_collected_run(conn)
    cid = add(conn, r, "The first stable release of SQLite came out in 2004.", r["p"](0), "SQLite runs as a library inside")
    claims.check_integrity(conn, cid, settings)
    result, _ = run_verifier(conn, settings, cid, verdict("unsupported", used=[], unsupported=["the release year"], date="missing"))
    assert result["verdict"] == "unsupported" and result["passages"] == [] and result["evidence_ids"] == []
    claim = claims.get_claim(conn, r["run"], cid)
    assert (claim["status"], claim["semantic_verdict"], claim["eligible_for_report"]) == ("unsupported", "unsupported", False)
    stray, _ = run_verifier(conn, settings, add(conn, r, "Another invented claim about releases.", r["p"](0), Q0), verdict("unsupported", used=["S99-P99"]))
    assert stray["verdict"] == "unsupported"  # an unshown ID is ignored, not promoted to an error


def test_integrity_and_semantic_outcomes_are_reported_separately(client, verify_env):
    conn = db.connect(client.db_path)
    r = seed_collected_run(conn)
    conn.close()
    from tests.fakes import claim, draft

    mesh = FakeMesh(draft=draft(claim(r["sq1"], Q0, ("S9-P9", Q0)), claim(r["sq1"], Q2, (r["p"](2), Q2))),
                    verify=verdict("unsupported", used=[r["p"](2)]))
    verify_env(mesh)
    client.post(f"/runs/{r['run']}/verify")
    by_text = {c["text"]: c for c in client.get(f"/runs/{r['run']}/claims").json()}
    failed, judged_ = by_text[Q0], by_text[Q2]
    assert (failed["integrity_verdict"], failed["semantic_verdict"]) == ("failed", None)  # never judged: not a semantic rejection
    assert (judged_["integrity_verdict"], judged_["semantic_verdict"]) == ("passed", "unsupported")
    v = client.get(f"/runs/{r['run']}/verification").json()
    assert v["by_integrity"] == {"failed": 1, "passed": 1} and v["by_semantic"] == {"none": 1, "unsupported": 1}
