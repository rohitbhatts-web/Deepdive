"""Regression tests for the coverage bookkeeping defects found in the preserved end-to-end retry run (offline: scripted FIXTURE replies only, no network).

Observed in that run: (1) a claim added by a corrective round was cut from the coverage input by a character cap, so it never received a relevance judgment;
(2) the subject backstop rejected claims that named the subject without repeating a run-wide qualifier word or in another inflected form, and the rejected
claims kept their model-assigned direct-answer role, so the report said "no cited claim is about X" next to claims about X shown as direct answers.
Nothing here names a particular subject: the fixtures are the HTTP world of test_answer_relevance and invented two-subject strings.
"""

import json

import pytest

from backend import claims, correction
from backend.agents import coverage
from backend.agents.obligations import ensure_obligations
from backend.budget import Budget
from backend.llm import LLM
from tests.fakes import FakeMesh
from tests.test_answer_relevance import (
    CLAIM,
    _assess_q1,
    _derived_world,
    assess,
    build_world,
    observed_judgments,
    report,
    statuses,
)
from tests.test_answer_relevance import reply as fixture_reply

# --- the subject backstop: shared words, inflections ------------------------------------------------------------------------------------------------


def test_a_word_every_subject_of_the_run_shares_is_not_required_in_a_claim():
    subjects = ["Acme alpha", "Acme beta", "Acme alpha and Acme beta"]
    shared = correction.shared_subject_stems(subjects)
    assert shared == {correction._stem("acme")}
    assert correction.mentions("Acme alpha", "The alpha mode is slower.", ignore_stems=shared)
    assert not correction.mentions("Acme alpha", "The beta mode is slower.", ignore_stems=shared)  # the distinguishing word is still required
    assert correction.mentions("Acme alpha and Acme beta", "Alpha is fast and beta is safe.", ignore_stems=shared)
    assert not correction.mentions("Acme alpha and Acme beta", "Alpha is fast.", ignore_stems=shared)
    assert not correction.mentions("Acme alpha", "The alpha mode is slower.")  # without the shared stems the old behaviour is unchanged


def test_meaningful_common_words_are_ignored_only_while_a_distinguishing_word_remains():
    shared = correction.shared_subject_stems(["HTTP/2 transport", "HTTP/3 transport"])
    assert shared == set()  # each subject is nothing but shared words and an identifier: they all identify it
    assert not correction.mentions("HTTP/2 transport", "HTTP/2 was published in June 2022.", ignore_stems=shared)  # same entity, another aspect
    assert correction.mentions("HTTP/2 transport", "HTTP/2 uses TCP as its transport.", ignore_stems=shared)
    forced = {correction._stem("http"), correction._stem("transport")}  # even if a caller passed every word, the check never empties out
    assert not correction.mentions("HTTP/2 transport", "HTTP/2 was published in June 2022.", ignore_stems=forced)


def test_a_claim_naming_only_the_shared_word_or_a_different_entity_is_not_accepted():
    shared = correction.shared_subject_stems(["Acme alpha", "Acme beta"])
    assert not correction.mentions("Acme alpha", "Acme is a company founded in 1990.", ignore_stems=shared)
    assert not correction.mentions("Acme alpha", "Acme beta is slower.", ignore_stems=shared)
    assert not correction.mentions("Acme alpha", "Zeta is unrelated to Acme gamma.", ignore_stems=shared)


def test_overlapping_subjects_where_one_is_only_the_shared_words_ignore_nothing():
    shared = correction.shared_subject_stems(["Acme alpha", "Acme alpha beta"])  # 'acme alpha' is the whole of one subject, so those words identify it
    assert shared == set()
    assert not correction.mentions("Acme alpha beta", "Acme beta is slower.", ignore_stems=shared)


def test_a_single_subject_run_ignores_nothing():
    assert correction.shared_subject_stems(["Acme alpha"]) == set()
    assert correction.shared_subject_stems(["Acme alpha", "acme alpha"]) == set()
    shared = correction.shared_subject_stems(["memory cache", "memory cache"])
    assert not correction.mentions("memory cache", "The cache is remote.", ignore_stems=shared)
    assert not correction.mentions("Acme", "Nothing relevant here.", ignore_stems={correction._stem("acme")})


def test_ignoring_shared_words_does_not_change_how_negation_is_treated():
    shared = correction.shared_subject_stems(["Acme alpha", "Acme beta"])
    # a lexical gate cannot read negation; the verifier and the assessor decide what the claim means, and the gate never creates an answer
    assert correction.mentions("Acme alpha", "The alpha mode is not recommended.", ignore_stems=shared)
    assert correction.mentions("Acme alpha", "Acme alpha is not recommended.")


@pytest.mark.parametrize("a,b", [("merge", "merging"), ("merge", "merged"), ("merges", "merge"), ("rebase", "rebasing"), ("branch", "branches")])
def test_inflected_forms_of_a_word_share_a_stem(a, b):
    assert correction._stem(a) == correction._stem(b)


@pytest.mark.parametrize("a,b", [("index", "inode"), ("tcp", "udp"), ("quic", "quit")])
def test_unrelated_short_words_do_not_share_a_stem(a, b):
    assert correction._stem(a) != correction._stem(b)


def test_a_claim_naming_the_subject_in_another_inflection_is_accepted_by_the_backstop(conn, settings):
    w = _derived_world(conn, settings, "HTTP/2 publishing")  # the claim says "published"
    a = _assess_q1(conn, settings, w)
    assert a["status"] == "addressed" and "subject_not_mentioned" not in a["notes"]


def test_the_backstop_only_ever_downgrades_it_never_creates_an_answer(conn, settings):
    w = _derived_world(conn, settings, "the current revision of the HTTP/3 specification")  # the model says addressed for a claim about HTTP/2
    a = _assess_q1(conn, settings, w)
    assert a["status"] == "unresolved" and a["supporting_claim_ids"] == []


# --- roles agree with the status a rule sets, scoped to the claim-obligation pair --------------------------------------------------------------------

def test_a_rule_downgrade_by_the_backstop_also_demotes_the_persisted_roles(conn, settings):
    w = _derived_world(conn, settings, "the current revision of the HTTP/3 specification")  # the only claim is about HTTP/2
    a = _assess_q1(conn, settings, w)
    assert a["status"] == "unresolved" and a["supporting_claim_ids"] == []
    row = conn.execute("SELECT role, reason, origin FROM claim_relevance WHERE claim_id = ?", (w["ids"]["date2"],)).fetchone()
    assert (row["role"], row["origin"]) == ("relevant_context", "rule") and row["reason"] == coverage.SUBJECT_NOT_IN_CLAIM


def test_the_report_does_not_show_a_claim_as_a_direct_answer_to_a_requirement_it_just_failed(conn, settings):
    w = _derived_world(conn, settings, "the current revision of the HTTP/3 specification")
    _assess_q1(conn, settings, w)
    rep = report(conn, w["run"])
    shown_as_direct = {c["claim_id"] for s in rep["sections"] for c in s["claims"]}
    assert w["ids"]["date2"] not in shown_as_direct
    assert any("no cited claim is about" in g["gap"] for g in rep["unresolved_requirements"])


def test_a_claim_rejected_for_one_obligation_keeps_its_direct_role_for_another(conn, settings):
    w = build_world(conn, settings, ["Publication date of the current revision of the HTTP/2 specification"], fallback=False)
    mesh = FakeMesh(obligations={"requirements": [{"requirement_key": "Q1.R1", "obligations": [
        {"subject": "HTTP/2 specification", "dimension": "publication date"}, {"subject": "HTTP/3 specification", "dimension": "publication date"}]}]})
    ensure_obligations(conn, LLM(mesh, settings), Budget(settings), settings, w["run"])
    date2 = w["ids"]["date2"]
    scripted = {"obligations": [{"obligation_key": k, "status": "addressed", "gap": "", "missing_kind": "none",
                                 "claim_roles": [{"claim_id": date2, "role": "direct_answer", "reason": "FIXTURE: states the date"}]} for k in ("Q1.R1.O1", "Q1.R1.O2")]}
    coverage.assess(conn, LLM(FakeMesh(coverage=scripted), settings), Budget(settings), settings, w["run"], 1)
    rows = {r["obligation_key"]: r for r in conn.execute(
        "SELECT o.obligation_key, oa.status, cr.role, cr.origin FROM claim_relevance cr JOIN obligation_assessments oa ON oa.id = cr.obligation_assessment_id"
        " JOIN coverage_obligations o ON o.id = oa.obligation_id WHERE cr.claim_id = ?", (date2,))}
    assert (rows["Q1.R1.O1"]["status"], rows["Q1.R1.O1"]["role"]) == ("addressed", "direct_answer")  # the matching subject keeps the credit
    assert (rows["Q1.R1.O2"]["status"], rows["Q1.R1.O2"]["role"], rows["Q1.R1.O2"]["origin"]) == ("unresolved", "relevant_context", "rule")
    rep = report(conn, w["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == w["sq"]["date2"])
    assert [c["claim_id"] for c in sec["claims"]] == [date2]  # still the section's direct answer


# --- every eligible claim reaches the assessor; shortened evidence and unseen claims are different things ---------------------------------------------

def _coverage_user_message(mesh):
    call = next(c for c in mesh.calls if "Coverage Assessor" in c["messages"][0]["content"])
    return call["messages"][-1]["content"]


def _eligible(conn, w):
    return {i: claims.get_claim(conn, w["run"], i) for i in w["ids"].values()}


def _event(conn):
    return json.loads(conn.execute("SELECT payload FROM audit_events WHERE event_type = 'coverage_assessed' ORDER BY id DESC").fetchone()[0])


def test_the_cap_counts_the_separators_between_blocks(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    eligible = _eligible(conn, w)
    full = [coverage._claim_block(c, *coverage._EVIDENCE_TIERS[0]) for c in eligible.values()]
    exact = len("\n".join(full))
    assert exact == sum(map(len, full)) + len(full) - 1
    blocks, shown, shortened = coverage._pack_claims(eligible, exact)
    assert shown == set(eligible) and shortened is None and blocks == full  # exactly the cap: nothing is shortened
    blocks, shown, shortened = coverage._pack_claims(eligible, exact - 1)  # over the cap only because of one separator
    assert shown == set(eligible) and shortened and shortened["claim_ids"]
    assert len("\n".join(blocks)) <= exact - 1


def test_shortened_evidence_keeps_claim_text_and_cited_passage_ids_and_is_reported_apart_from_claims_not_shown(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    eligible = _eligible(conn, w)
    bare_min = len("\n".join(coverage._claim_block(c, *coverage._EVIDENCE_TIERS[-1]) for c in eligible.values()))
    s = settings.model_copy(update={"coverage_input_chars": bare_min})  # forces the last tier
    mesh = FakeMesh(coverage={"obligations": []})
    coverage.assess(conn, LLM(mesh, s), Budget(s), s, w["run"], 1)
    text = _coverage_user_message(mesh)
    for c in eligible.values():
        assert c["text"] in text and all(f'passage="{e["passage"]["id"]}"' in text for e in c["evidence"])
    ev = _event(conn)
    assert ev["claims_not_shown"] == []
    assert ev["evidence_shortened"]["limits"] == list(coverage._EVIDENCE_TIERS[-1]) and ev["evidence_shortened"]["claim_ids"]


def test_the_full_evidence_is_kept_when_everything_fits(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    coverage.assess(conn, LLM(FakeMesh(coverage={"obligations": []}), settings), Budget(settings), settings, w["run"], 1)
    ev = _event(conn)
    assert ev["claims_not_shown"] == [] and "evidence_shortened" not in ev


def _only_first_claim_fits(conn, settings, w):
    eligible = _eligible(conn, w)
    first = coverage._claim_block(next(iter(eligible.values())), *coverage._EVIDENCE_TIERS[-1])
    return settings.model_copy(update={"coverage_input_chars": len(first) + 1}), eligible


def test_claims_that_cannot_fit_are_recorded_as_not_shown_and_omitted_with_a_reason(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    s, eligible = _only_first_claim_fits(conn, settings, w)
    judged = [("RFC publication date for HTTP/2", "addressed", [(w["ids"]["date2"], "direct_answer", "FIXTURE: states the date")], "", "none")]
    coverage.assess(conn, LLM(FakeMesh(coverage=fixture_reply(conn, w["run"], judged)), s), Budget(s), s, w["run"], 1)
    unseen = set(_event(conn)["claims_not_shown"])
    assert unseen == set(eligible) - {next(iter(eligible))}  # the shortfall is never silent
    rep = report(conn, w["run"])
    om = {o["claim_id"]: o["reason"] for o in rep["omissions"]}
    assert unseen <= set(om) and all("no answer-relevance judgment" in om[i] for i in unseen)  # unknown stays unknown: not "judged irrelevant"
    assert all(CLAIM[k] not in rep["markdown"] for k, i in w["ids"].items() if i in unseen)


def test_an_obligation_cannot_be_resolved_by_a_claim_the_assessor_was_never_shown(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    s, _ = _only_first_claim_fits(conn, settings, w)
    rows = [("name of the transport protocol used by HTTP/3", "addressed", [(w["ids"]["quic"], "direct_answer", "FIXTURE: names it")], "", "none")]
    coverage.assess(conn, LLM(FakeMesh(coverage=fixture_reply(conn, w["run"], rows)), s), Budget(s), s, w["run"], 1)
    a = statuses(conn, w["run"])["name of the transport protocol used by HTTP/3"]
    assert a["status"] == "unresolved" and a["supporting_claim_ids"] == [] and w["ids"]["quic"] in a["notes"]["dropped_claim_ids"]


def test_an_unknown_claim_id_cannot_resolve_an_obligation(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    rows = [("name of the transport protocol used by HTTP/3", "addressed", [(999_999, "direct_answer", "FIXTURE: not a claim of this run")], "", "none")]
    assess(conn, settings, w["run"], rows)
    a = statuses(conn, w["run"])["name of the transport protocol used by HTTP/3"]
    assert a["status"] == "unresolved" and a["supporting_claim_ids"] == [] and a["notes"]["dropped_claim_ids"] == [999_999]


def test_an_explanation_citing_a_claim_that_was_not_shown_is_rejected(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    s, _ = _only_first_claim_fits(conn, settings, w)
    rows = [("RFC publication date for HTTP/2", "addressed", [(w["ids"]["date2"], "direct_answer", "FIXTURE: states the date")], f"see claim {w['ids']['quic']}", "none")]
    coverage.assess(conn, LLM(FakeMesh(coverage=fixture_reply(conn, w["run"], rows)), s), Budget(s), s, w["run"], 1)
    a = statuses(conn, w["run"])["RFC publication date for HTTP/2"]
    assert a["notes"]["explanation_rejected"] and a["status"] == "unresolved"


def test_a_judged_claim_is_never_listed_as_omitted_for_lack_of_a_judgment(conn, settings):
    w = build_world(conn, settings, ["RFC publication date for HTTP/2"])
    assess(conn, settings, w["run"], observed_judgments(w))
    rep = report(conn, w["run"])
    assert not [o for o in rep["omissions"] if "no answer-relevance judgment" in o["reason"]]
