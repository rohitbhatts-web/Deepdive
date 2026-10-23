"""The development-only relevance check: its plumbing, limits and refusals, with SCRIPTED model replies (no model, search or fetch). The scripted replies
test how the check records and compares; they say nothing about how a real model would judge."""

import json

import pytest

from backend.config import Settings
from backend.llm import LLM
from evaluation import relevance_check as rc
from tests.fakes import FakeMesh, obligations, verdict

HEADER = "RFC 9113 HTTP/2 June 2022 Obsoletes: 7540, 8740 Published: June 2022"
RENAME = "On 28 October 2018 Mark Nottingham proposed renaming HTTP-over-QUIC to HTTP/3."
QUIC = "HTTP/3 uses QUIC, a transport layer network protocol, over UDP."
STORED = {6: ("The HTTP/2 specification was published as RFC 9113 in June 2022.", HEADER, "RFC 9113 HTTP/2 June 2022"),
          5: ("The article describes that on 28 October 2018, Mark Nottingham proposed renaming HTTP-over-QUIC to HTTP/3.", RENAME, "proposed renaming HTTP-over-QUIC to HTTP/3"),
          3: ("HTTP/3 uses QUIC as its transport layer protocol.", QUIC, "HTTP/3 uses QUIC")}


@pytest.fixture(autouse=True)
def stored_evidence(monkeypatch):
    def fake(db_path, claim_id):
        text, passage, quote = STORED[claim_id]
        return {"claim": text, "passage_text": passage, "quote": quote, "url": f"https://example.test/{claim_id}", "title": "Stored page", "content_hash": f"h{claim_id}"}
    monkeypatch.setattr(rc, "stored_claim_evidence", fake)


def roles(*pairs):
    return [{"claim_id": c, "role": r, "reason": "FIXTURE"} for c, r in pairs]


def coverage_reply(key_roles):
    return {"obligations": [{"obligation_key": f"{k}.O1", "status": "addressed", "claim_roles": roles(*rr), "gap": "", "missing_kind": "none"} for k, rr in key_roles]}


def mesh(a4_role="direct_answer"):
    return FakeMesh(
        verify=[verdict("supported", used=[f"S{i}-P0"]) for i in range(1, 7)],
        obligations=[obligations(Q1_R1=[("HTTP/2", "original publication date")], Q2_R1=[("HTTP/2", "publication date of the current revision")],
                                 Q3_R1=[("HTTP/3", "RFC publication date")], Q4_R1=[("HTTP/3", "transport protocol")]),
                     obligations(Q5_R1=[("SQLite", "initial release date")], Q6_R1=[("PostgreSQL", "default isolation level")])],
        coverage=[coverage_reply([("Q1.R1", [(1, "direct_answer")]), ("Q2.R1", [(2, "direct_answer")]), ("Q3.R1", [(3, "relevant_context")]), ("Q4.R1", [(4, a4_role)])]),
                  coverage_reply([("Q5.R1", [(5, "relevant_context")]), ("Q6.R1", [(6, "direct_answer")])])])


def run(tmp_path, fake):
    s = Settings(_env_file=None, mesh_api_key="", drafter_model="fx-d", verifier_model="fx-v")
    return rc.execute(LLM(fake, s), s, out=tmp_path / "result.json", scratch=tmp_path / "scratch.db", with_charges=False)


def test_the_expectations_are_written_down_as_ai_assisted_development_expectations_over_two_domains():
    cases = rc.load_cases()
    assert "AI-assisted development expectations" in cases["provenance"] and "not independent human ground truth" in cases["provenance"]
    assert len(cases["domains"]) >= 2 and "development only" in cases["scope"]
    ids = [c["id"] for d in cases["domains"] for c in d["cases"]]
    assert ids == ["A1-original-vs-revision", "A2-explicit-revision-question", "A3-off-topic-milestone", "A4-direct-non-date", "B1-off-topic-milestone", "B2-direct-non-date"]
    assert {c["kind"] for d in cases["domains"] for c in d["cases"]} >= {"original publication versus a later revision", "a supported but off-topic milestone",
                                                                          "an explicit question about the revised document's publication date",
                                                                          "a supported direct answer to a non-date question"}
    assert "contains no such wording" in cases["unavailable"]  # reported, not invented


def test_the_real_current_stages_run_in_order_within_the_limits_and_the_result_records_expected_versus_observed(tmp_path):
    fake = mesh()
    result = run(tmp_path, fake)
    assert fake.steps().count("verify") == 6 and fake.steps().count("obligations") == 2 and fake.steps().count("coverage") == 2
    assert result["totals"]["model_requests"] == 10 <= 2 * result["limits_per_domain"]["max_model_requests"]
    assert all(d["model_requests"] <= result["limits_per_domain"]["max_model_requests"] for d in result["domains"])
    by = {c["id"]: c for d in result["domains"] for c in d["cases"]}
    assert all(c["all_agree"] for c in by.values()), {k: v["observed"] for k, v in by.items() if not v["all_agree"]}
    a1, a2 = by["A1-original-vs-revision"]["observed"], by["A2-explicit-revision-question"]["observed"]
    assert a1["temporal_scope"] == "original" and a1["claim_role"] == "not_direct_answer" and a1["roles"][0]["origin"] == "rule"  # the code, not the assessor, kept the revision out
    assert a2["temporal_scope"] == "revision" and a2["claim_role"] == "direct_answer" and a2["requirement_status"] == "addressed"
    assert by["A3-off-topic-milestone"]["observed"]["section_context_claims"] == [3]  # the milestone is listed as context, not as the answer
    saved = json.loads((tmp_path / "result.json").read_text(encoding="utf-8"))
    assert "AI-assisted development expectations" in saved["provenance"] and saved["domains"][0]["cases"][0]["expected"]["claim_role"] == "not_direct_answer"


def test_a_disagreement_with_an_expectation_is_reported_not_hidden(tmp_path):
    result = run(tmp_path, mesh(a4_role="relevant_context"))  # the scripted assessor calls the direct non-date answer context
    a4 = next(c for d in result["domains"] for c in d["cases"] if c["id"] == "A4-direct-non-date")
    assert a4["all_agree"] is False and a4["observed"]["claim_role"] == "not_direct_answer" and a4["agrees_with_expectation"]["claim_role"] is False


def test_a_result_or_scratch_database_is_never_overwritten_or_silently_repeated(tmp_path):
    run(tmp_path, mesh())
    with pytest.raises(SystemExit, match="already exists"):
        run(tmp_path, mesh())


def test_the_dry_run_sends_nothing_and_states_limits_estimate_and_expectations(capsys):
    assert rc.main([]) == 0
    out = capsys.readouterr().out
    assert "dry run: nothing was sent" in out and "AI-assisted development expectations" in out and "max_model_requests" in out and "$" in out and "A1-original-vs-revision" in out
    est = rc.estimate(rc.load_cases())
    assert est["cap_requests"] == 16 and est["typical_requests"] == 10 and est["cap_usd"] < 0.1


# --- separated reporting: a case blocked before relevance is never a relevance pass -----------------------------------------------------------------

EXPECT = {"claim_role": "not_direct_answer", "requirement": "not_addressed", "section": "no_direct_answer"}


def classify(**over):
    base = {"expect": EXPECT, "verdict": "supported", "eligible": True, "roles": [{"role": "relevant_context", "origin": "model", "reason": "r"}], "requirement_status": "unresolved",
                "answered": False, "claim_id": 1, "answer_ids": [], "context_ids": [1], "omission": None}
    return rc.classify_case(**{**base, **over})


def test_a_claim_blocked_by_its_semantic_verdict_is_relevance_not_exercised_even_when_the_outcome_fields_match():
    r = classify(verdict="contradicted", eligible=False, roles=[], context_ids=[], omission="not eligible: contradicted")
    assert r["status"] == "relevance_not_exercised" and r["relevance"]["exercised"] is False and r["relevance"]["decision"] is None
    assert "blocked before relevance" in r["relevance"]["why"] and r["components"]["claim_role"] == "not exercised"
    assert r["components"]["requirement"] == r["components"]["section"] == "matches" and "not evidence about relevance" in r["note"]  # vacuous matches are labelled


def test_a_missing_judgment_is_not_a_context_classification():
    r = classify(roles=[], context_ids=[], omission="no answer-relevance judgment for this claim in coverage round 0")
    assert r["status"] == "relevance_not_exercised" and r["relevance"]["decision"] is None and "no relevance judgment" in r["relevance"]["why"]
    assert r["report_placement"].startswith("omitted") and r["components"]["claim_role"] == "not exercised"


def test_an_exercised_case_is_a_pass_only_when_every_component_matches():
    assert classify()["status"] == "pass" and classify()["report_placement"] == "context"
    differs = classify(requirement_status="addressed")
    assert differs["status"] == "differs" and differs["components"]["requirement"] == "differs" and differs["components"]["claim_role"] == "matches"
    wrong = classify(roles=[{"role": "direct_answer", "origin": "model", "reason": "r"}])
    assert wrong["status"] == "differs" and wrong["relevance"]["decision"] == "direct_answer"


def test_new_results_carry_the_separated_fields_and_an_old_result_is_re_read_without_being_modified(tmp_path):
    result = run(tmp_path, mesh())
    cases = {c["id"]: c for d in result["domains"] for c in d["cases"]}
    assert cases["A1-original-vs-revision"]["separated"]["status"] == "pass" and cases["A1-original-vs-revision"]["claim_id"] == 1
    saved = (tmp_path / "result.json").read_bytes()
    reread = rc.interpret(json.loads(saved), tmp_path / "scratch.db")
    assert (tmp_path / "result.json").read_bytes() == saved  # the original is untouched
    assert reread["summary"] == {"pass": 6, "differs": 0, "relevance_not_exercised": 0} and "not a new experiment" in reread["provenance"]


def test_re_reading_a_result_where_the_verifier_blocked_a_claim_and_the_assessor_skipped_one(tmp_path):
    fake = mesh()
    fake.replies["verify"][0] = verdict("contradicted", used=["S1-P0"], conflicts=["fixture conflict"])  # A1's claim is blocked by the verifier
    fake.replies["coverage"][1] = coverage_reply([("Q5.R1", []), ("Q6.R1", [(6, "direct_answer")])])  # B1: the assessor lists no role for the claim
    rc.execute(LLM(fake, Settings(_env_file=None, mesh_api_key="", drafter_model="fx-d", verifier_model="fx-v")),
               Settings(_env_file=None, mesh_api_key="", drafter_model="fx-d", verifier_model="fx-v"), out=tmp_path / "result.json", scratch=tmp_path / "scratch.db", with_charges=False)
    reread = rc.interpret(json.loads((tmp_path / "result.json").read_text(encoding="utf-8")), tmp_path / "scratch.db")
    by = {c["id"]: c for d in reread["domains"] for c in d["cases"]}
    assert by["A1-original-vs-revision"]["status"] == "relevance_not_exercised" and by["B1-off-topic-milestone"]["status"] == "relevance_not_exercised"
    assert by["B1-off-topic-milestone"]["report_placement"].startswith("omitted") and reread["summary"]["pass"] == 4


# --- the prepared A1/A2 follow-up ---------------------------------------------------------------------------------------------------------------------

def test_the_follow_up_keeps_the_recorded_expectations_uses_its_own_limits_and_new_artifact_paths():
    cases = rc.load_cases()
    derived, out, scratch = rc.follow_up(cases, "a1a2-v5")
    ids = [c["id"] for d in derived["domains"] for c in d["cases"]]
    assert ids == ["A1-original-vs-revision", "A2-explicit-revision-question"]
    original = {c["id"]: c for d in cases["domains"] for c in d["cases"]}
    assert all(c == original[c["id"]] for d in derived["domains"] for c in d["cases"])  # expectations are exactly the ones recorded before the first run
    assert derived["limits_per_domain"] == {"max_model_requests": 6, "max_total_output_tokens": 3000, "max_elapsed_seconds": 90, "max_search_queries": 0,
                                            "max_page_fetches": 0, "per_call_timeout_seconds": 60}
    assert out != rc.RESULT and scratch != rc.SCRATCH and out.name == "relevance_check_a1a2_v5.json" and scratch.name == "relevance-check-a1a2-v5.db"
    est = rc.estimate(derived)
    assert est["typical_requests"] == 4 and est["cap_requests"] == 6 and est["typical_usd"] == 0.014 and est["cap_usd"] == 0.026


def test_the_follow_up_dry_run_sends_nothing_and_creates_nothing(capsys):
    _, result, scratch = rc.follow_up(rc.load_cases(), "a1a2-v5")

    def state():  # the approved run may already have created these files; a dry run must not create or touch anything
        return [(f.exists(), f.stat().st_mtime_ns if f.exists() else None) for f in (result, scratch)]

    before = state()
    assert rc.main(["--follow-up", "a1a2-v5"]) == 0
    out = capsys.readouterr().out
    assert "FOLLOW-UP a1a2-v5" in out and "dry run: nothing was sent" in out and "relevance_check_a1a2_v5.json" in out and "relevance-check-a1a2-v5.db" in out
    assert "NOT changed" in out and state() == before


def test_a_follow_up_run_writes_only_its_own_files_and_records_a_blocked_a1_honestly(tmp_path):
    derived, _, _ = rc.follow_up(rc.load_cases(), "a1a2-v5")
    fake = FakeMesh(verify=[verdict("contradicted", used=["S1-P0"], conflicts=["fixture"]), verdict("supported", used=["S2-P0"])],
                    obligations=obligations(Q1_R1=[("HTTP/2", "original publication date")], Q2_R1=[("HTTP/2", "publication date of the current revision")]),
                    coverage=coverage_reply([("Q1.R1", []), ("Q2.R1", [(2, "direct_answer")])]))
    s = Settings(_env_file=None, mesh_api_key="", drafter_model="fx-d", verifier_model="fx-v")
    result = rc.execute(LLM(fake, s), s, out=tmp_path / "fu.json", scratch=tmp_path / "fu.db", with_charges=False, cases=derived)
    assert result["follow_up"]["name"] == "a1a2-v5" and result["totals"]["model_requests"] == 4 <= derived["limits_per_domain"]["max_model_requests"]
    by = {c["id"]: c["separated"] for d in result["domains"] for c in d["cases"]}
    assert by["A1-original-vs-revision"]["status"] == "relevance_not_exercised"  # blocked again: recorded as such, not forced to pass
    assert by["A1-original-vs-revision"]["semantic_eligibility"] == {"verdict": "contradicted", "eligible": False}
    assert by["A2-explicit-revision-question"]["relevance"]["exercised"] is True
